from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import uuid
import json
import re
from openai import OpenAI
from docx import Document
from resume_ai_analyzer import (
    analyze_resume_with_openai,
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_with_ocr,
    check_ats_compatibility,
    generate_ai_resume_content
)

app = Flask(__name__, static_url_path='/static')
CORS(app, supports_credentials=True, resources={r"/*": {"origins": "*"}})

UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

@app.route('/generate-resume', methods=['POST'])
def generate_resume():
    try:
        data = request.json
        result = generate_ai_resume_content(
            data.get("name", ""),
            data.get("email", ""),
            data.get("phone", ""),
            data.get("location", ""),
            data.get("summary", ""),
            data.get("education", ""),
            data.get("experience", ""),
            data.get("certifications", ""),
            data.get("skills", ""),
            data.get("languages", ""),
            data.get("hobbies", "")
        )
        return jsonify({"success": True, "html": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/health')
def health():
    return "OK"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
