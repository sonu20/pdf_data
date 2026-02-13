import streamlit as st
import pdfplumber
import re
import pandas as pd
from io import BytesIO

# ------------------------------------------------------------
# 1. Parse Date Sheet (generic) -> dict {paper_id: (date, subject, paper_code)}
# ------------------------------------------------------------
def parse_date_sheet(pdf_file):
    """Extract all exam entries from the date sheet PDF."""
    exam_map = {}   # key = paper_id (str), value = (date, subject, paper_code)
    current_date = None

    # Patterns
    date_pattern = re.compile(r'^(\d{2}\.\d{2}\.\d{4})')
    paper_id_pattern = re.compile(r'\b(\d{5})\b')
    # Paper code patterns: new (24L6.0-XXX-123) or old (MC-101, ENG-101, etc.)
    code_pattern = re.compile(r'(\b[\w\.]+?-\d{3}\b)')

    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            lines = text.split('\n')
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # 1. Check for date at beginning of line
                date_match = date_pattern.match(line)
                if date_match:
                    current_date = date_match.group(1)
                    # The same line may also contain a paper entry (some PDFs put date + subject on same line)
                    # We'll process the rest of the line for paper IDs below

                # 2. Find all 5-digit paper IDs in this line
                paper_ids = paper_id_pattern.findall(line)
                if not paper_ids:
                    continue

                # Extract paper code (if any)
                code_match = code_pattern.search(line)
                paper_code = code_match.group(1) if code_match else ""

                # Extract subject name: from start of line up to the paper code (or paper ID if no code)
                if paper_code:
                    subject = line[:line.index(paper_code)].strip()
                else:
                    # Fallback: take text before the first paper ID
                    first_pid_pos = line.find(paper_ids[0])
                    subject = line[:first_pid_pos].strip()

                # Clean subject: remove stray 'New', 'NEP Scheme', etc. but keep descriptive name
                subject = re.sub(r'\s+', ' ', subject)  # normalize spaces
                subject = subject.strip(' -')

                # Store each paper ID with current date (if date exists)
                for pid in paper_ids:
                    if current_date:
                        exam_map[pid] = (current_date, subject, paper_code)
                    else:
                        st.warning(f"Found Paper ID {pid} without a preceding date. Skipped.")

    return exam_map

# ------------------------------------------------------------
# 2. Parse Roll List (generic) -> list of {roll_no, student_name, paper_ids}
# ------------------------------------------------------------
def parse_roll_list(pdf_file):
    """Extract each student's roll number, name, and all enrolled paper IDs."""
    students = []
    paper_id_pattern = re.compile(r'\b(\d{5})\b')

    with pdfplumber.open(pdf_file) as pdf:
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

    # Split by "Roll No" to get each student's block
    chunks = re.split(r'(Roll No\s+)', full_text)
    for i in range(1, len(chunks), 2):
        block = chunks[i] + chunks[i+1] if i+1 < len(chunks) else chunks[i]

        # Extract Roll Number
        roll_match = re.search(r'Roll No\s*(\d+)', block)
        roll_no = roll_match.group(1) if roll_match else ""

        # Extract Student Name (look for "Name" field)
        name_match = re.search(r'Name\s+([A-Z\s]+?)\s+(Father|SUBJECTS|Roll|$)', block, re.IGNORECASE)
        if name_match:
            student_name = name_match.group(1).strip()
        else:
            # Fallback: take the line after the Roll No line
            lines = block.split('\n')
            for idx, line in enumerate(lines):
                if 'Roll No' in line and idx+1 < len(lines):
                    candidate = lines[idx+1].strip()
                    if candidate and not re.search(r'\d', candidate):
                        student_name = candidate
                        break
            else:
                student_name = "UNKNOWN"

        # Extract all paper IDs (5-digit numbers) from the block
        paper_ids = paper_id_pattern.findall(block)
        # Remove duplicates but keep order
        paper_ids = list(dict.fromkeys(paper_ids))

        if roll_no and student_name and paper_ids:
            students.append({
                'roll_no': roll_no,
                'student_name': student_name,
                'paper_ids': paper_ids
            })

    return students

# ------------------------------------------------------------
# 3. Merge & Build Schedule
# ------------------------------------------------------------
def build_schedule(exam_map, students):
    """Create DataFrame with full schedule."""
    rows = []
    for student in students:
        roll = student['roll_no']
        name = student['student_name']
        for pid in student['paper_ids']:
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
                # Paper ID not found in date sheet – maybe it's a different session or error
                rows.append({
                    'Roll No': roll,
                    'Student Name': name,
                    'Exam Date': 'NOT IN DATE SHEET',
                    'Subject': 'UNKNOWN',
                    'Paper Code': '',
                    'Paper ID': pid
                })
    df = pd.DataFrame(rows)
    return df

# ------------------------------------------------------------
# 4. Streamlit UI
# ------------------------------------------------------------
st.set_page_config(page_title="Generic Exam Schedule Generator", layout="wide")
st.title("Generic Exam Schedule Generator")
st.markdown("""
Upload the **Date Sheet PDF** and the **Roll List PDF** (any department).  
The app will automatically match every student's enrolled papers with the exam dates and subjects.
""")

col1, col2 = st.columns(2)
with col1:
    date_file = st.file_uploader("Date Sheet (PDF)", type="pdf", key="date")
with col2:
    roll_file = st.file_uploader("Roll List (PDF)", type="pdf", key="roll")

if date_file and roll_file:
    with st.spinner("Parsing PDFs... This may take a few seconds."):
        exam_map = parse_date_sheet(date_file)
        students = parse_roll_list(roll_file)
        df = build_schedule(exam_map, students)

    if not df.empty:
        st.success(f"Done! Found {len(exam_map)} paper entries in date sheet, "
                   f"{len(students)} students in roll list, and generated {len(df)} schedule rows.")

        # Preview
        st.subheader("Preview (first 30 rows)")
        st.dataframe(df.head(30), use_container_width=True)

        # Download Excel
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Exam Schedule')
        output.seek(0)

        st.download_button(
            label="Download Excel Schedule",
            data=output,
            file_name="exam_schedule.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # Optional: show missing mappings
        missing = df[df['Exam Date'] == 'NOT IN DATE SHEET']
        if not missing.empty:
            st.warning(f"{len(missing)} entries have paper IDs that were not found in the date sheet. "
                       f"They are marked as 'NOT IN DATE SHEET'. Check your PDFs.")
    else:
        st.error("No data could be extracted. Please check the PDF files.")
