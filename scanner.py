import streamlit as st
import pdfplumber
import re
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="Universal Exam Schedule Builder", layout="wide")
st.title("📋 Universal Exam Schedule Builder")
st.markdown("Upload **Date Sheet PDF** and **Roll List PDF**. The app matches every student's papers with exam dates.")

# ------------------------------------------------------------
# 1. Parse Date Sheet (generic)
# ------------------------------------------------------------
def parse_date_sheet(pdf_file):
    exam_map = {}          # paper_id -> (date, subject, paper_code)
    current_date = None

    # Patterns
    date_pattern = re.compile(r'^(\d{2}[\.\-]\d{2}[\.\-]\d{4})')   # dd.mm.yyyy or dd-mm-yyyy
    paper_id_pattern = re.compile(r'\b(\d{5})\b')                  # 5-digit paper ID
    code_pattern = re.compile(r'(\b[\w\.]+?-\d{3,4}\b)')           # paper code like 24L6.0-ENG-101 or MC-101

    with pdfplumber.open(pdf_file) as pdf:
        all_text = []
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                all_text.append(text)
        # Join pages with newline to preserve separation
        full_text = "\n".join(all_text)

    # Process line by line
    lines = full_text.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Check if line starts with a date
        date_match = date_pattern.match(line)
        if date_match:
            current_date = date_match.group(1).replace('-', '.')   # normalize to dots

        # If we have a current date, look for paper IDs
        if current_date:
            paper_ids = paper_id_pattern.findall(line)
            if not paper_ids:
                continue

            # Try to extract paper code
            code_match = code_pattern.search(line)
            paper_code = code_match.group(1) if code_match else ""

            # Extract subject: everything before the paper code (or before the first paper ID)
            if paper_code:
                subject = line[:line.index(paper_code)].strip()
            else:
                # Fallback: take text before the first paper ID
                first_pid_pos = line.find(paper_ids[0])
                subject = line[:first_pid_pos].strip()

            # Clean subject: remove extra spaces, stray marks
            subject = re.sub(r'\s+', ' ', subject).strip(' -')

            for pid in paper_ids:
                exam_map[pid] = (current_date, subject, paper_code)

    return exam_map

# ------------------------------------------------------------
# 2. Parse Roll List (using tables + text fallback)
# ------------------------------------------------------------
def parse_roll_list(pdf_file):
    students = []
    paper_id_pattern = re.compile(r'\b(\d{5})\b')

    # First attempt: extract tables
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                # Table structure: each row likely has Roll No, Reg No, Name, Father, Subjects
                for row in table:
                    if not row or len(row) < 5:
                        continue
                    # Try to find roll number in first column
                    roll_cell = str(row[0] if row[0] else "")
                    if "Roll No" in roll_cell or re.search(r'\d{9,}', roll_cell):   # heuristic
                        # Extract roll number using regex
                        roll_match = re.search(r'(\d+)', roll_cell)
                        roll_no = roll_match.group(1) if roll_match else ""
                    else:
                        continue   # not a student row

                    # Name is usually in column 2 or 3
                    name = ""
                    for col in [2, 1, 3]:   # try different columns
                        if col < len(row) and row[col]:
                            name_cell = str(row[col])
                            if name_cell and not re.search(r'\d{5,}', name_cell):
                                name = name_cell.strip()
                                break
                    if not name:
                        name = "UNKNOWN"

                    # Gather all paper IDs from the entire row (all columns)
                    row_text = " ".join([str(c) for c in row if c])
                    paper_ids = paper_id_pattern.findall(row_text)
                    paper_ids = list(dict.fromkeys(paper_ids))   # unique, keep order

                    if roll_no and paper_ids:
                        students.append({
                            'roll_no': roll_no,
                            'student_name': name,
                            'paper_ids': paper_ids
                        })

    # If tables didn't work, fallback to text chunking
    if not students:
        st.info("Table extraction gave no results, falling back to text scanning...")
        with pdfplumber.open(pdf_file) as pdf:
            full_text = ""
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"

        # Split by "Roll No"
        chunks = re.split(r'(Roll No\s+)', full_text)
        for i in range(1, len(chunks), 2):
            block = chunks[i] + chunks[i+1] if i+1 < len(chunks) else chunks[i]

            roll_match = re.search(r'Roll No\s*(\d+)', block)
            if not roll_match:
                continue
            roll_no = roll_match.group(1)

            # Extract name: look for "Name" field
            name_match = re.search(r'Name\s+([A-Z\s]+?)\s+(Father|SUBJECTS|Roll|$)', block, re.IGNORECASE)
            if name_match:
                student_name = name_match.group(1).strip()
            else:
                # Fallback: line after Roll No
                lines = block.split('\n')
                name = ""
                for idx, line in enumerate(lines):
                    if 'Roll No' in line and idx+1 < len(lines):
                        name = lines[idx+1].strip()
                        break
                student_name = name if name else "UNKNOWN"

            paper_ids = paper_id_pattern.findall(block)
            paper_ids = list(dict.fromkeys(paper_ids))

            if roll_no and paper_ids:
                students.append({
                    'roll_no': roll_no,
                    'student_name': student_name,
                    'paper_ids': paper_ids
                })

    return students

# ------------------------------------------------------------
# 3. Merge
# ------------------------------------------------------------
def build_schedule(exam_map, students):
    rows = []
    for s in students:
        roll = s['roll_no']
        name = s['student_name']
        for pid in s['paper_ids']:
            if pid in exam_map:
                date, subject, code = exam_map[pid]
                rows.append({
                    'Roll No': roll,
                    'Student Name': name,
                    'Exam Date': date,
                    'Subject': subject,
                    'Paper Code': code,
                    'Paper ID': pid
                })
            else:
                rows.append({
                    'Roll No': roll,
                    'Student Name': name,
                    'Exam Date': 'NOT IN DATE SHEET',
                    'Subject': 'UNKNOWN',
                    'Paper Code': '',
                    'Paper ID': pid
                })
    return pd.DataFrame(rows)

# ------------------------------------------------------------
# 4. UI
# ------------------------------------------------------------
col1, col2 = st.columns(2)
with col1:
    date_file = st.file_uploader("📅 Date Sheet PDF", type="pdf", key="date")
with col2:
    roll_file = st.file_uploader("🧑‍🎓 Roll List PDF", type="pdf", key="roll")

if date_file and roll_file:
    with st.spinner("🔍 Parsing PDFs..."):
        exam_map = parse_date_sheet(date_file)
        students = parse_roll_list(roll_file)
        df = build_schedule(exam_map, students)

    # Debug expanders – show what was extracted
    with st.expander("📄 Preview extracted date sheet entries"):
        if exam_map:
            st.write(f"Found {len(exam_map)} paper entries. Sample:")
            sample = {k: exam_map[k] for k in list(exam_map)[:5]}
            st.json(sample)
        else:
            st.error("No paper entries found in date sheet. Showing first 500 chars of extracted text:")
            # Re-extract raw text for debugging
            with pdfplumber.open(date_file) as pdf:
                raw = "".join([p.extract_text() or "" for p in pdf.pages])[:500]
            st.text(raw)

    with st.expander("🧑‍🎓 Preview extracted students"):
        if students:
            st.write(f"Found {len(students)} students. Sample:")
            st.json(students[:3])
        else:
            st.error("No students found in roll list. Showing first 500 chars of extracted text:")
            with pdfplumber.open(roll_file) as pdf:
                raw = "".join([p.extract_text() or "" for p in pdf.pages])[:500]
            st.text(raw)

    if not df.empty:
        st.success(f"✅ Generated {len(df)} schedule rows for {df['Roll No'].nunique()} students.")
        st.subheader("📊 Preview (first 30)")
        st.dataframe(df.head(30), use_container_width=True)

        # Download Excel
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Schedule')
        output.seek(0)
        st.download_button("📥 Download Excel", data=output,
                           file_name="exam_schedule.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.error("❌ No data could be extracted. Please check the PDFs and the debug info above.")
