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
    atsfix_flag = request.form.get('atsfix')  # Expected from frontend

    if not file or file.filename == '':
        return jsonify({'error': 'No file uploaded'}), 400

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    file.save(filepath)

    try:
        atsfix = atsfix_flag == 'true'
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
        return jsonify({'error': 'Resume text could not be extracted'}), 400

    prompt = f"""
You are a career coach and expert cover letter writer. Based on the resume content and the job title and company name below, write a compelling cover letter.

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
    except:
        return jsonify({'error': 'Failed to generate cover letter.'}), 500

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

@app.route('/fix-suggestion', methods=['POST'])
def fix_suggestion():
    file = request.files.get('file')
    suggestion = request.form.get('suggestion')
    if not file or not suggestion:
        return jsonify({'error': 'File and suggestion are required'}), 400

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
        return jsonify({'error': 'Could not extract text from resume'}), 400

    prompt = f"""
You are an expert resume editor. Apply the following fix to this resume:

Fix: "{suggestion}"

Resume:
{resume_text}

Now return the updated resume only, with the fix applied. Don't explain anything.
    """

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a resume fixing assistant."},
            {"role": "user", "content": prompt}
        ]
    )

    fixed_text = response.choices[0].message.content.strip()
    filename = f"fixed_resume_{uuid.uuid4().hex[:6]}.txt"
    filepath = os.path.join(STATIC_FOLDER, filename)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(fixed_text)

    return send_from_directory(STATIC_FOLDER, filename, as_attachment=True)

@app.route('/final-resume', methods=['POST'])
def final_resume():
    file = request.files.get('file')
    fixes = request.form.get('fixes')
    if not file or not fixes:
        return jsonify({'error': 'File and fixes are required'}), 400

    try:
        fixes_list = json.loads(fixes)
    except:
        return jsonify({'error': 'Fixes must be valid JSON'}), 400

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

    resume_text = resume_text[:12000]
    all_fixes_text = "\n".join(
        f"- {fix['suggestion']}\n  Apply: {fix['fixedText']}" for fix in fixes_list
    )[:3000]

    prompt = f"""
You're an AI resume editor. Here's the original resume and a list of improvements to apply. Return only the final updated resume, no explanation.

Resume:
{resume_text}

Fixes to Apply:
{all_fixes_text}
    """

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a resume editing assistant."},
            {"role": "user", "content": prompt}
        ]
    )

    raw_text = response.choices[0].message.content.strip()
    clean_text = re.sub(r'[^\x09\x0A\x0D\x20-\x7E\u00A0-\uFFFF]', '', raw_text)

    filename = f"final_resume_{uuid.uuid4().hex[:6]}.docx"
    filepath = os.path.join(STATIC_FOLDER, filename)

    try:
        doc = Document()
        for line in clean_text.splitlines():
            if line.strip():
                doc.add_paragraph(line.strip())
        doc.save(filepath)
    except Exception as e:
        return jsonify({'error': f'DOCX saving error: {str(e)}'}), 500

    return send_from_directory(STATIC_FOLDER, filename, as_attachment=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
