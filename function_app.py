import logging
import os
import json
from datetime import datetime

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

# Trigger: new blob uploaded to input container
@my_app.blob_trigger(arg_name="myblob", path="input", connection="AzureWebJobsStorage")
@my_app.durable_client_input(client_name="client")
async def blob_trigger(myblob: func.InputStream, client):
    logging.info(f"Python blob trigger function processed blob "
                 f"Name: {myblob.name} "
                 f"Blob Size: {myblob.length} bytes")
    blobName = myblob.name.split("/")[1]
    await client.start_new("process_document", client_input=blobName)

# Orchestration function
@my_app.orchestration_trigger(context_name="context")
def process_document(context):
    blobName: str = context.get_input()

    first_retry_interval_in_milliseconds = 5000
    max_number_of_attempts = 3
    retry_options = df.RetryOptions(first_retry_interval_in_milliseconds, max_number_of_attempts)

    # Download the PDF from Blob Storage and use Document Intelligence Form Recognizer to analyze its contents.
    extracted_text = yield context.call_activity_with_retry("analyze_pdf", retry_options, blobName)
    # Send the analyzed contents to Azure OpenAI to generate a summary.
    summary = yield context.call_activity_with_retry("summarize_text", retry_options, extracted_text)
    # Save the summary to a new file and upload it back to storage.
    output = yield context.call_activity_with_retry("write_doc", retry_options, {"blobName": blobName, "summary": summary})

    logging.info(f"Successfully uploaded summary to {output}")
    return output

# Activity: Analyze PDF via Form Recognizer
@my_app.activity_trigger(input_name='blobName')
def analyze_pdf(blobName):
    logging.info(f"in analyze_pdf activity")
    container_client = blob_service_client.get_container_client("input")
    blob_client = container_client.get_blob_client(blobName)
    blob = blob_client.download_blob().read()

    endpoint = os.environ["DocumentIntelligenceEndpoint"]
    key = os.environ["DocumentIntelligenceKey"]
    client = DocumentAnalysisClient(endpoint=endpoint, credential=AzureKeyCredential(key))

    poller = client.begin_analyze_document("prebuilt-layout", document=blob, locale="en-US")
    result = poller.result().pages

    full_text = ""
    for page in result:
        for line in page.lines:
            full_text += line.content

    return full_text

# Activity: Summarize text using Azure OpenAI
@my_app.activity_trigger(input_name='results')
@my_app.generic_input_binding(arg_name="response", type="textCompletion", data_type=func.DataType.STRING, prompt="Can you explain what the following text is about? {results}", model="%OpenAIDeploymentName%", connection="AzureOpenAI")
def summarize_text(results, response: str):
    logging.info(f"in summarize_text activity")
    response_json = json.loads(response)
    logging.info(response_json['content'])
    return response_json

# Activity: Write summary to output container
@my_app.activity_trigger(input_name='results')
def write_doc(results):
    logging.info(f"in write_doc activity")
    container_client = blob_service_client.get_container_client("output")

    summary = results['blobName'] + "-" + str(datetime.now())
    sanitized_summary = summary.replace(".", "-")
    filename = sanitized_summary + ".txt"

    logging.info("uploading to blob " + results['summary']['content'])
    container_client.upload_blob(name=filename, data=results['summary']['content'])
    return str(filename)