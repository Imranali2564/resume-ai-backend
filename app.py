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
    check_ats_compatibility
)

app = Flask(__name__, static_url_path='/static')
CORS(app)

UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

@app.route('/upload', methods=['POST'])
def upload_resume():
    file = request.files.get('file')
    atsfix = request.form.get('atsfix') == 'true'

    if not file or file.filename == '':
        return jsonify({'error': 'No file uploaded'}), 400

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    file.save(filepath)

    try:
        result = analyze_resume_with_openai(filepath, atsfix=atsfix)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ... keep rest of app.py code unchanged
