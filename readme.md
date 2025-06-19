# 📚 CST8919 Lab: Building a Text Summarization Pipeline with Azure Functions and OpenAI

## 🎥 Video Demonstration

[![Watch the video](https://img.youtube.com/vi/DySvMtmHvRc/hqdefault.jpg)](https://youtu.be/DySvMtmHvRc)


## 🛠️ Technologies Used

| Service / Tool              | Purpose                               |
|----------------------------|----------------------------------------|
| Azure Durable Functions     | Orchestration of PDF analysis workflow |
| Azure Blob Storage          | File input/output storage              |
| Azure Form Recognizer       | Text extraction from uploaded PDFs     |
| Azure OpenAI (GPT-3.5)      | Text summarization                     |
| Python (v3.9+)              | Function app development language      |
| VS Code + Core Tools        | Local development & deployment         |
| YouTube                     | Hosting the demo video                 |

---

## ⚙️ How It Works

1. 📥 Upload PDF to `input` blob container.
2. 📤 Trigger Durable Function orchestrator.
3. 📄 `analyze_pdf`: extract text via Form Recognizer.
4. 🧠 `summarize_text`: summarize via Azure OpenAI.
5. 📄 `write_doc`: store summary in `output` container.

## Triggers Overview
# 1 Warmup Trigger
```bash
@my_app.timer_trigger(schedule="0 */5 * * * *", use_monitor=False)
def warmup_function(my_timer: func.TimerRequest):
    logging.info("Warmup trigger executed to pre-load dependencies and reduce cold start.")
    pass
```
Purpose: Pre-loads dependencies to reduce cold start latency.
# 2 Blob Trigger
```bash
@my_app.blob_trigger(arg_name="myblob", path="input", connection="AzureWebJobsStorage")
@my_app.durable_client_input(client_name="client")
async def blob_trigger(myblob: func.InputStream, client):
    logging.info(f"Python blob trigger function processed blob Name: {myblob.name} Blob Size: {myblob.length} bytes")
    blobName = myblob.name.split("/")[1]
    await client.start_new("process_document", client_input=blobName)
```
Purpose: Initiates the Durable Functions orchestration when a new PDF is uploaded to the input container.
# 3 Orchestration Trigger
```bash
@my_app.orchestration_trigger(context_name="context")
def process_document(context):
    blobName: str = context.get_input()
    first_retry_interval_in_milliseconds = 5000
    max_number_of_attempts = 3
    retry_options = df.RetryOptions(first_retry_interval_in_milliseconds, max_number_of_attempts)
    extracted_text = yield context.call_activity_with_retry("analyze_pdf", retry_options, blobName)
    summary = yield context.call_activity_with_retry("summarize_text", retry_options, extracted_text)
    output = yield context.call_activity_with_retry("write_doc", retry_options, {"blobName": blobName, "summary": summary})
    logging.info(f"Successfully uploaded summary to {output}")
    return output
```
Purpose: Manages the workflow by coordinating analyze_pdf, summarize_text, and write_doc activities with retry logic.
# 4 Analyze PDF Activity Trigger
```bash
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
```
Purpose: Extracts text from a PDF using Azure Document Intelligence.
# 5 Summarize Text Activity Trigger
```bash
@my_app.activity_trigger(input_name='results')
@my_app.generic_input_binding(arg_name="response", type="textCompletion", data_type=func.DataType.STRING, prompt="Can you explain what the following text is about? {results}", model="%OpenAIDeploymentName%", connection="AzureOpenAI__")
def summarize_text(results, response: str):
    logging.info(f"in summarize_text activity")
    response_json = json.loads(response)
    logging.info(response_json['content'])
    return response_json
```
Purpose: Summarizes extracted text using Azure OpenAI.

# 6 Write summary Trigger
```bash
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
```
## What I Learned
Developing an Azure Durable Functions app with Python.
Here, I must point out that I successfully tested calling GPT-3.5 locally, but after deploying to Azure, the call failed, causing the write_doc trigger to fail as well.

Integrating Azure Blob Storage, Document Intelligence, and OpenAI.
Configuring application settings and handling environment variables.
Implementing retry logic and warm-up triggers to improve reliability.
Troubleshooting configuration errors (e.g., missing Endpoint or Key).
## Challenges Faced
Configuration Errors: Resolved issues with missing AzureWebJobsStorage or AzureOpenAI__Key.

Cold Start Latency: Addressed with a warmup trigger.
API Permissions: Enabled Managed Identity and assigned roles.
Logging: Debugged log output for workflow tracking.

## How to Improve in a Real-World Scenario
Error Handling: Add comprehensive exception handling.
Security: Use Azure Key Vault for API keys.
Scalability: Upgrade to a premium plan.
Monitoring: Add custom KQL alerts.
Validation: Implement PDF input validation.

👨‍💻 Author

Shaoxian Duan
