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
CORS(app, resources={r"/*": {"origins": "https://resumefixerpro.com"}})

UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def extract_personal_info(text):
    lines = text.strip().splitlines()
    top = "\n".join(lines[:20])  # Top 20 lines
    name = lines[0].strip() if lines else ""
    email_match = re.search(r'[\w\.-]+@[\w\.-]+', top, re.IGNORECASE)
    phone_match = re.search(r'(\+91[\s-]?)?[6-9]\d{9}', top)
    location_match = re.search(r'(?i)(Location[:\-]?\s*)?([A-Za-z\s]{3,},?\s*[A-Za-z]*)', top)
    fallback_location = lines[1].strip() if len(lines) > 1 else ""
    return {
        "name": name,
        "email": email_match.group(0) if email_match else "",
        "phone": phone_match.group(0) if phone_match else "",
        "location": location_match.group(2).strip() if location_match else fallback_location
    }

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

@app.route('/generate-cover-letter', methods=['POST'])
def generate_cover_letter():
    file = request.files.get('file')
    job_title = request.form.get('job_title')
    company_name = request.form.get('company_name')

    if not file or not job_title or not company_name:
        return jsonify({'error': 'File, job title, and company name are required'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    ext = os.path.splitext(filename)[1].lower()
    if ext == '.pdf':
        resume_text = extract_text_from_pdf(filepath) or extract_text_with_ocr(filepath)
    elif ext == '.docx':
        resume_text = extract_text_from_docx(filepath)
    else:
        return jsonify({'error': 'Unsupported file format'}), 400

    if not resume_text.strip():
        return jsonify({'error': 'Could not extract text from resume'}), 400

    info = extract_personal_info(resume_text)
    name = info["name"]
    email = info["email"]
    phone = info["phone"]
    location = info["location"]

    prompt = f"""
You are a career coach and expert cover letter writer. Based on the resume content and the job title and company name below, write a compelling cover letter.

Candidate Details:
Name: {name}
Email: {email}
Phone: {phone}
Location: {location}

Resume:
{resume_text}

Job Title: {job_title}
Company Name: {company_name}

Cover Letter:
"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a professional cover letter writing assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        cover_letter = response.choices[0].message.content.strip()
        return jsonify({"cover_letter": cover_letter})
    except Exception as e:
        return jsonify({'error': f'Failed to generate cover letter: {str(e)}'}), 500

@app.route('/download-cover-letter', methods=['POST'])
def download_cover_letter():
    data = request.get_json()
    text = data.get("cover_letter", "").strip()
    if not text:
        return jsonify({"error": "No content provided"}), 400
    filename = f"cover_letter_{uuid.uuid4().hex[:6]}.docx"
    filepath = os.path.join(STATIC_FOLDER, filename)
    doc = Document()
    for line in text.splitlines():
        if line.strip():
            doc.add_paragraph(line.strip())
    doc.save(filepath)
    return send_from_directory(STATIC_FOLDER, filename, as_attachment=True)

@app.route('/generate-ai-resume', methods=['POST'])
def generate_ai_resume():
    try:
        data = request.json
        html = generate_ai_resume_content(
            data.get("name", ""),
            data.get("email", ""),
            data.get("phone", ""),
            data.get("location", ""),
            "",
            data.get("education", ""),
            data.get("experience", ""),
            data.get("certifications", ""),
            data.get("skills", ""),
            data.get("languages", ""),
            data.get("hobbies", "")
        )
        return jsonify({"success": True, "html": html})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
