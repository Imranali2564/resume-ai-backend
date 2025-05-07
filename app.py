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
CORS(app, resources={r"/*": {"origins": "https://resumefixerpro.com"}})

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

@app.route('/resume-score', methods=['POST'])
def resume_score():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    file.save(filepath)
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".pdf":
        resume_text = extract_text_from_pdf(filepath) or extract_text_with_ocr(filepath)
    elif ext == ".docx":
        resume_text = extract_text_from_docx(filepath)
    else:
        return jsonify({'error': 'Unsupported file format'}), 400
    if not resume_text.strip():
        return jsonify({'error': 'No extractable text found in resume'}), 400
    prompt = f"""
You are a professional resume reviewer. Give a resume score between 0 and 100 based on:
- Formatting and readability
- Grammar and professionalism
- Use of action verbs and achievements
- Keyword optimization for ATS
- Overall impression and completeness

Resume:
{resume_text}

Just return a number between 0 and 100, nothing else.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a strict but fair resume scoring assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        score_raw = response.choices[0].message.content.strip()
        score = int(''.join(filter(str.isdigit, score_raw)))
        return jsonify({"score": max(0, min(score, 100))})
    except:
        return jsonify({"score": 70})

@app.route('/check-ats', methods=['POST'])
def check_ats():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    file.save(filepath)
    try:
        ats_result = check_ats_compatibility(filepath)
        return jsonify({'ats_report': ats_result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/generate-ai-resume', methods=['POST'])
def generate_ai_resume():
    try:
        data = request.json

        name = data.get("name", "")
        email = data.get("email", "")
        phone = data.get("phone", "")
        location = data.get("location", "")
        education = data.get("education", "")
        experience = data.get("experience", "")
        skills = data.get("skills", "")
        certifications = data.get("certifications", "")
        languages = data.get("languages", "")
        hobbies = data.get("hobbies", "")

        prompt = f"""
You are a professional resume writer. Based on the following user input, write a professional resume in clean HTML.
Use small, clean <h3> tags with style 'font-size:0.95rem; line-height:1.3; color:#222; margin-bottom:4px; border-bottom:1px solid #ccc;'.
Return only the HTML body (no <html> or <body> tags). Format like a real resume.

User Info:
Name: {name}
Email: {email}
Phone: {phone}
Location: {location}
Education: {education}
Experience: {experience}
Skills: {skills}
Certifications: {certifications}
Languages: {languages}
Hobbies: {hobbies}
        """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an expert HTML resume generator."},
                {"role": "user", "content": prompt}
            ]
        )

        html = response.choices[0].message.content.strip()
        return jsonify({"success": True, "html": html})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
