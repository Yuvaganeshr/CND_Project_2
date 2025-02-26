import os
import json
import requests
from flask import Flask, redirect, request, render_template, send_from_directory, url_for
from google.cloud import storage
import google.generativeai as genai
from google.oauth2 import service_account
from google.cloud import secretmanager

app = Flask(__name__)
app.secret_key = "your-secret-key"

os.makedirs('files', exist_ok=True)

# Fetch the service account key and set up authentication using it
def get_service_account_key():
    secret_client = secretmanager.SecretManagerServiceClient()
    secret_name = "projects/orbital-nirvana-449316-k3/secrets/sakey/versions/latest"
    response = secret_client.access_secret_version(name=secret_name)
    service_account_key = response.payload.data.decode("UTF-8")
    return json.loads(service_account_key)

# Fetch Gemini API key from Secret Manager
def get_gemini_api_key():
    secret_client = secretmanager.SecretManagerServiceClient()
    secret_name = "projects/orbital-nirvana-449316-k3/secrets/geminiApi/versions/latest"
    response = secret_client.access_secret_version(name=secret_name)
    gemini_api_key = response.payload.data.decode("UTF-8")
    return gemini_api_key

# Authenticate with service account using the downloaded key
def authenticate_with_service_account(service_account_key):
    creds = service_account.Credentials.from_service_account_info(service_account_key)
    return creds

# Get the Gemini API key
gemini_api_key = get_gemini_api_key()

# Configure Gemini API client
genai.configure(api_key=gemini_api_key)

# Google Cloud Storage client setup
bucket_name = 'picart0'
service_account_key = get_service_account_key()
storage_client = storage.Client(credentials=authenticate_with_service_account(service_account_key))

def list_images_in_bucket():
    # List all images in the bucket and fetch their metadata (title and description)
    bucket = storage_client.get_bucket(bucket_name)
    blobs = bucket.list_blobs()

    image_metadata = []
    image_files = {}

    for blob in blobs:
        if blob.name.lower().endswith(('.jpg', '.jpeg', '.png')):  # Image file
            image_name = blob.name  # Get image filename
            description_file = image_name.rsplit('.', 1)[0] + '.txt'  # Get the corresponding .txt file
            image_files[image_name] = description_file  # Pair the image with its description file
    
    for image_name, description_file in image_files.items():
        titleAndDescription = []
        with open(os.path.join('files',description_file), 'r') as d:
            titleAndDescription = d.read().splitlines()

        image_metadata.append({
            "name": image_name,
            "metadata": {
                "title": titleAndDescription[0],
                "description": titleAndDescription[1]
            }
        })

    return image_metadata

@app.route('/')
def home():
    # Fetch the list of images from the bucket
    images = list_images_in_bucket()
    return render_template('index.html', images=images)

@app.route('/files/<user_id>/<filename>')
def files(user_id, filename):
    return send_from_directory(os.path.join('files', user_id), filename)

@app.route('/upload', methods=['POST'])
def upload():
    user_id = "default_user"  # Use a default user ID since login is removed
    user_folder = os.path.join('files', user_id)
    os.makedirs(user_folder, exist_ok=True)
    file = request.files['form_file']
    filename = file.filename
    if filename == '':
        return "No file selected", 400
    if not filename.lower().endswith(('.jpg', '.jpeg', '.png')):
        return "Invalid file format. Only .jpg, .jpeg, and .png files are allowed.", 400
    local_image_path = os.path.join(user_folder, filename)
    file.save(local_image_path)
    print(f"File saved at: {local_image_path}")
    image_file = upload_to_gemini(local_image_path)
    title, description = generate_description(image_file)
    local_text_path = os.path.join(user_folder, os.path.splitext(filename)[0] + '.txt')
    with open(local_text_path, 'w') as text_file:
        text_file.write(f"{title}\n{description}")
    with open(local_text_path, 'rb') as text_file:
        upload_blob(bucket_name, text_file, os.path.basename(local_text_path), user_id)
    file.seek(0)
    upload_blob(bucket_name, file, os.path.basename(local_image_path), user_id)
    return redirect('/')

def upload_to_gemini(image_path, mime_type="image/jpeg"):
    print(image_path)
    file = genai.upload_file(image_path, mime_type=mime_type)
    print(f"Uploaded file '{file.display_name}' as: {file.uri}")
    return file

def generate_description(image_file):
    generation_config = {
        "temperature": 1,
        "top_p": 0.95,
        "top_k": 64,
        "max_output_tokens": 8192,
        "response_mime_type": "application/json",
    }
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        generation_config=generation_config,
    )
    chat_session = model.start_chat(
        history=[ 
            {"role": "user", "parts": ["Generate title and a description for an image\n"]},
            {"role": "model", "parts": ["Please provide me with the image you want a title and description for!"]},
            {"role": "user", "parts": [image_file, "Generate a title and a description."]},
        ]
    )
    response = chat_session.send_message("INSERT_INPUT_HERE")
    try:
        parsed_response = json.loads(response.text)
        title = parsed_response.get("title", "No Title Available")
        description = parsed_response.get("description", "No Description Available")
        return title, description
    except json.JSONDecodeError:
        print("Error decoding JSON response.")
        return "Error generating title", "Error generating description"

def upload_blob(bucket_name, file, blob_name, user_id):
    bucket = storage_client.get_bucket(bucket_name)
    blob = bucket.blob(os.path.join(user_id, blob_name))
    blob.upload_from_file(file)
    print(f"Uploaded blob '{blob_name}' to '{bucket_name}/{user_id}'.")

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True,port=int(os.environ.get("PORT", 8080)))
