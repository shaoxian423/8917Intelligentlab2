import logging
import os
import json
from datetime import datetime
import openai  # 添加 OpenAI 库

import azure.functions as func
import azure.durable_functions as df
from azure.storage.blob import BlobServiceClient
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential

# Initialize Durable Functions app
my_app = df.DFApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Cloud Azure blob storage client using environment variable
connection_string = os.environ.get("AzureWebJobsStorage")
if not connection_string:
    raise ValueError("AzureWebJobsStorage connection string not found in environment variables.")
blob_service_client = BlobServiceClient.from_connection_string(connection_string)

# Warmup trigger to reduce cold start (runs every 5 minutes)
@my_app.timer_trigger(schedule="0 */5 * * * *", use_monitor=False)
def warmup_function(my_timer: func.TimerRequest):
    logging.info("Warmup trigger executed to pre-load dependencies and reduce cold start.")
    # Optional: Pre-load clients or dependencies
    pass

# Trigger: new blob uploaded to input container
@my_app.blob_trigger(arg_name="myblob", path="input/{name}", connection="AzureWebJobsStorage")
@my_app.durable_client_input(client_name="client")
async def blob_trigger(myblob: func.InputStream, client):
    logging.info(f"Python blob trigger function processed blob "
                 f"Name: {myblob.name} "
                 f"Blob Size: {myblob.length} bytes")
    blob_name = myblob.name.split("/")[1]  # Extract filename
    await client.start_new("process_document", client_input=blob_name)

# Orchestration function
@my_app.orchestration_trigger(context_name="context")
def process_document(context):
    blob_name: str = context.get_input()
    retry_options = df.RetryOptions(5000, 3)  # 5 seconds, 3 attempts

    extracted_text = yield context.call_activity_with_retry("analyze_pdf", retry_options, blob_name)
    summary = yield context.call_activity_with_retry("summarize_text", retry_options, extracted_text)
    output = yield context.call_activity_with_retry("write_doc", retry_options, {"blobName": blob_name, "summary": summary})

    logging.info(f"Successfully uploaded summary to {output}")
    return output

# Activity: Analyze PDF via Form Recognizer
@my_app.activity_trigger(input_name="blobName")
def analyze_pdf(blobName):
    logging.info(f"in analyze_pdf activity")
    container_client = blob_service_client.get_container_client("input")
    blob_client = container_client.get_blob_client(blobName)
    blob = blob_client.download_blob().readall()  # Use readall() for complete content

    endpoint = os.environ["DocumentIntelligenceEndpoint"]
    key = os.environ["DocumentIntelligenceKey"]
    client = DocumentAnalysisClient(endpoint=endpoint, credential=AzureKeyCredential(key))

    poller = client.begin_analyze_document("prebuilt-layout", document=blob, locale="en-US")
    result = poller.result().pages

    full_text = ""
    for page in result:
        for line in page.lines:
            full_text += line.content + "\n"  # Add newline for readability
    return full_text

# Activity: Summarize text using Azure OpenAI (direct API call)
@my_app.activity_trigger(input_name='results')
def summarize_text(results):
    logging.info(f"in summarize_text activity - fallback mode")


    endpoint = os.environ["AzureOpenAI__Endpoint"]
    key = os.environ["AzureOpenAI__Key"]
    deployment = os.environ["OpenAIDeploymentName"]

    openai.api_type = "azure"
    openai.api_key = key
    openai.api_base = endpoint
    openai.api_version = "2023-05-15"  

    try:
        response = openai.ChatCompletion.create(
            engine=deployment,
            messages=[
                {"role": "system", "content": "You are an assistant that summarizes documents."},
                {"role": "user", "content": f"Can you explain what the following text is about? {results}"}
            ]
        )
        content = response['choices'][0]['message']['content']
        logging.info(f"OpenAI response: {content}")
        return {"content": content}
    except Exception as e:
        logging.error(f"Failed to call OpenAI API: {e}")
        raise

# Activity: Write summary to output container
@my_app.activity_trigger(input_name='results')
def write_doc(results):
    logging.info(f"in write_doc activity")
    container_client = blob_service_client.get_container_client("output")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{results['blobName']}-{timestamp}.txt"

    try:
        content = results['summary'].get('content', 'No summary generated')
        container_client.upload_blob(name=filename, data=content, overwrite=True)
        logging.info(f"Summary uploaded as {filename}")
        return filename
    except Exception as e:
        logging.error(f"Failed to upload summary: {e}")
        raise