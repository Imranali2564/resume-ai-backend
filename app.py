from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import uuid
import json
from openai import OpenAI
from docx import Document
from resume_ai_analyzer import (
    analyze_resume_with_openai,
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_with_ocr
)

app = Flask(__name__, static_url_path='/static')
CORS(app)

UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.route('/upload', methods=['POST'])
def upload_resume():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    try:
        result = analyze_resume_with_openai(filepath)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/fix-suggestion', methods=['POST'])
def fix_suggestion():
    file = request.files.get('file')
    suggestion = request.form.get('suggestion')
    if not file or not suggestion:
        return jsonify({'error': 'File and suggestion are required'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    extension = os.path.splitext(filepath)[1].lower()
    if extension == ".pdf":
        resume_text = extract_text_from_pdf(filepath)
        if not resume_text.strip():
            resume_text = extract_text_with_ocr(filepath)
    elif extension == ".docx":
        resume_text = extract_text_from_docx(filepath)
    else:
        return jsonify({'error': 'Unsupported file format'}), 400

    if not resume_text.strip():
        return jsonify({'error': 'Could not extract text from resume'}), 400

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
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
    fixed_filename = f"fixed_resume_{uuid.uuid4().hex[:6]}.docx"
    fixed_filepath = os.path.join(STATIC_FOLDER, fixed_filename)

    doc = Document()
    for line in fixed_text.split('\n'):
        doc.add_paragraph(line.strip())
    doc.save(fixed_filepath)

    return send_from_directory(STATIC_FOLDER, fixed_filename, as_attachment=True)

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

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    extension = os.path.splitext(filepath)[1].lower()
    if extension == ".pdf":
        resume_text = extract_text_from_pdf(filepath)
        if not resume_text.strip():
            resume_text = extract_text_with_ocr(filepath)
    elif extension == ".docx":
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

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a resume editing assistant."},
            {"role": "user", "content": prompt}
        ]
    )

    fixed_text = response.choices[0].message.content.strip()
    final_filename = f"final_resume_{uuid.uuid4().hex[:6]}.docx"
    final_filepath = os.path.join(STATIC_FOLDER, final_filename)

    doc = Document()
    for line in fixed_text.split('\n'):
        doc.add_paragraph(line.strip())
    doc.save(final_filepath)

    return send_from_directory(STATIC_FOLDER, final_filename, as_attachment=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
