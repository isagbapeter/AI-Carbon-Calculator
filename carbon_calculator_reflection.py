"""
Digital Workflow Carbon Footprint Calculator

Prerequisites:
    pip install openai pymupdf pypdf python-docx
    winget install ffmpeg
    Set OpenAI API Key = [$env:OPENAI_API_KEY = "Your Open AI Key"]
"""

import os
import re
import io
import json
import zipfile
import argparse
import csv
from pathlib import Path
from openai import OpenAI


#UK grid emissions factor
grid_factor_uk = 195.53  #gCO2e/kWh for device & router

#Global average carbon intensity
grid_factor_global = 471.0  #gCO2e/kWh for networks & datacentres

#Device power breakdown in Watts
device_power = 30
router_power = 5
network_power = 5
datacentre_power = 5

uk_power = device_power + router_power
global_power = network_power + datacentre_power

#SWDM energy intensity per GB
#Based on IEA (2022) energy data and ITU (2023) global traffic (5.29 ZB)
swdm_datacentre = 0.055
swdm_network = 0.059
swdm_user_device = 0.080
swdm_total = (swdm_datacentre + swdm_network + swdm_user_device)  # 0.194 kwh/gb

message_co2e = 0.8 #gram CO2e per message
reading_speed = 238 #words per minute
typing_speed = 40 #words per minute

#Openai GPT client
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


def ask_gpt(system_prompt: str, user_content: str, model: str = "gpt-4o-mini") -> str:
    "Send a prompt to GPT and return the text response"
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()



def time_based_co2e(total_hours: float) -> float:
    
    #Calculate CO2e in grams from total device time in hours.
    energy_uk = (uk_power / 1000) * total_hours
    energy_global = (global_power / 1000) * total_hours
    return (energy_uk * grid_factor_uk) + (energy_global * grid_factor_global)


def email_co2e(word_count: int, num_recipients: int) -> float:
    
    writing_hours = (word_count / typing_speed) / 60
    reading_hours = (word_count / reading_speed) / 60 * num_recipients
    total_hours = writing_hours + reading_hours
    return time_based_co2e(total_hours)


def document_text_co2e(word_count: int) -> float:
    
    writing_hours = (word_count / typing_speed) / 60
    reading_hours = (word_count / reading_speed) / 60
    total_hours = writing_hours + reading_hours
    return time_based_co2e(total_hours)


def image_co2e(file_size_bytes: int) -> float:
    
    #Calculate CO2e in grams for standalone and embedded images.
    file_size_gb = file_size_bytes / (1024 ** 3)
    energy_global = file_size_gb * (swdm_datacentre + swdm_network)
    energy_uk = file_size_gb * swdm_user_device
    return (energy_global * grid_factor_global) + (energy_uk * grid_factor_uk)


#WhatsApp parser
def parse_whatsapp(filepath: str) -> dict:
    
    # Counts messages in the whatsApp chat file
    # Uses OpenAI GPT if regex fails
    
    print(f"\n[WhatsApp] Reading: {filepath}")
    text = Path(filepath).read_text(encoding="utf-8", errors="replace")

    pattern = re.compile(
        r'^\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?\s*[-\u2013]\s*.+?:\s+',
        re.MULTILINE
    )
    regex_count = len(pattern.findall(text))

    if regex_count > 0:
        print(f"[WhatsApp] Regex found {regex_count} messages.")
        message_count = regex_count
    else:
        print("[WhatsApp] Regex found 0 messages — asking GPT to count...")
        chunk = text[:8000]
        system = (
            """
            You are a data extraction assistant.
            Count the total number of individual messages in this WhatsApp chat.
            Do not count system messages.
            Review your count before returning. Does it seem accurate given the file size and length of text?
            RESPOND WITH ONLY A JSON OBJECT: {"message_count": <integer>}

            """
        )
        response = ask_gpt(system, chunk)
        try:
            message_count = json.loads(response)["message_count"]
            if len(text) > 8000:
                scale = len(text) / 8000
                message_count = int(message_count * scale)
                print(f"[WhatsApp] File truncated; scaled count to {message_count}")
        except Exception as e:
            print(f"[WhatsApp] GPT parse failed ({e}). Defaulting to 0.")
            message_count = 0

    co2e = message_count * message_co2e
    print(f"[WhatsApp] {message_count} messages x {message_co2e}g = {co2e:.2f}g CO2e")
    return {
        "type": "WhatsApp",
        "source": Path(filepath).name,
        "message_count": message_count,
        "word_count": None,
        "duration_seconds": None,
        "recipients": None,
        "file_size_bytes": None,
        "co2e_g": co2e,
    }


#Email parser
def parse_emails(filepath: str) -> list[dict]:
    
    # Use OpenAI GPT to extract word counts and recipient counts from email threads
    print(f"\n[Emails] Reading: {filepath}")
    text = Path(filepath).read_text(encoding="utf-8", errors="replace")
    filename = Path(filepath).name

    system = """
    You are a data extraction assistant analysing exported email files.
    For each individual email found, extract:
        Word Count (words in the body only. Exclude headers like From/To/Sent/Subject, and quoted reply text). If there is no body text, use 0.
        recipients (number of recipients with To + Cc combined, minimum 1)

    Once you have identified all emails, review your output:
        have you correctly identified each email as a distinct message?
        Are any emails merged or missing?
        Does the word count per email seem accurate given the file size of that email?
    
    Correct any errors before returning.
    Always return at least one entry per email file, even if the body is empty.
    YOU MUST RETURN ONLY VALID JSON. DO NOT include any explanation, preamble, or markdown formatting.
    [
        {"email_index": 1, "word_count": 45, "recipients": 3},
        {"email_index": 2, "word_count": 0, "recipients": 1}
    ]
"""

    chunk_size = 12000
    all_emails = []
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]

    for chunk_idx, chunk in enumerate(chunks):
        print(f"[Emails] Processing chunk {chunk_idx + 1}/{len(chunks)}...")
        response = ask_gpt(system, chunk)
        try:
            emails = json.loads(response)
            all_emails.extend(emails)
        except Exception as e:
            print(f"[Emails] GPT parse failed on chunk {chunk_idx + 1} ({e}). Skipping.")

    # If OpenAI GPT returns nothing at all, log the file with 0 word count
    if not all_emails:
        print(f"[Emails] No emails detected in {filename} — logging as 0 word count.")
        all_emails = [{"email_index": 1, "word_count": 0, "recipients": 1}]

    results = []
    for i, email in enumerate(all_emails):
        wc   = int(email.get("word_count", 0))
        recp = int(email.get("recipients", 1))
        co2e = email_co2e(wc, recp)
        print(f"[Emails] Email {i+1}: {wc} words, {recp} recipients = {co2e:.4f}g CO2e")
        results.append({
            "type": "Email",
            "source": f"{filename} — email {i+1}",
            "message_count": None,
            "word_count": wc,
            "duration_seconds": None,
            "recipients": recp,
            "file_size_bytes": None,
            "co2e_g": co2e,
        })

    total = sum(r["co2e_g"] for r in results)
    print(f"[Emails] {len(results)} emails total =  {total:.4f}g CO2e")
    return results


#Document parser
document_extensions = {".pdf", ".docx", ".doc", ".txt"}

def extract_text_and_images(f: Path) -> tuple[str, float]:
    #Extract plain text and embedded image CO2e from a document file.

    ext = f.suffix.lower()

    #Read .txt files
    if ext == ".txt":
        try:
            return f.read_text(encoding="utf-8", errors="replace"), 0.0
        except Exception as e:
            print(f"[Document] Could not read {f.name}: {e}")
            return "", 0.0

    #PDF
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            import fitz

            #Text extraction via pypdf
            reader = PdfReader(str(f))
            text = " ".join(page.extract_text() or "" for page in reader.pages)

            #Embedded image extraction via PyMuPDF
            embedded_co2e = 0.0
            picture_count = 0
            doc = fitz.open(str(f))
            for page in doc:
                for img in page.get_images(full=True):
                    xref = img[0]
                    try:
                        base_image = doc.extract_image(xref)
                        size_bytes = len(base_image["image"])
                        embedded_co2e += image_co2e(size_bytes)
                        picture_count += 1
                    except Exception as e:
                        print(f"[Document] Could not extract embedded image ({e}). Skipping.")
            doc.close()

            if picture_count > 0:
                print(f"[Document] {f.name}: {picture_count} embedded image(s) found = {embedded_co2e:.6f}g CO2e")

            return text, embedded_co2e

        except Exception as e:
            print(f"[Document] Could not process {f.name}: {e}")
            return "", 0.0

    #DOCX
    if ext in {".docx", ".doc"}:
        try:
            from docx import Document as DocxDocument

            doc = DocxDocument(str(f))

            #Text extraction via python-docx
            text = " ".join(para.text for para in doc.paragraphs)

            #Embedded image extraction via part relationships in the docx zip container
            embedded_co2e = 0.0
            picture_count = 0
            for rel in doc.part.rels.values():
                if "image" in rel.reltype:
                    try:
                        image_bytes = rel.target_part.blob
                        size_bytes = len(image_bytes)
                        embedded_co2e += image_co2e(size_bytes)
                        picture_count += 1
                    except Exception as e:
                        print(f"[Document] Could not extract embedded image ({e}). Skipping.")

            if picture_count > 0:
                print(f"[Document] {f.name}: {picture_count} embedded image(s) found = {embedded_co2e:.6f}g CO2e")

            return text, embedded_co2e

        except Exception as e:
            print(f"[Document] Could not process {f.name}: {e}")
            return "", 0.0

    return "", 0.0


def collect_files_from_path(path: Path, extensions: set, exclude_names: set = set()) -> list:
    
    collected = []

    if path.is_file():
        if path.suffix.lower() == ".zip":
            #Extract matching files from zip into memory
            print(f"[Zip] Opening archive: {path.name}")
            with zipfile.ZipFile(path, "r") as zf:
                for entry in zf.infolist():
                    entry_name = Path(entry.filename).name
                    entry_ext  = Path(entry.filename).suffix.lower()
                    if entry_ext in extensions and entry_name not in exclude_names:
                        data = zf.read(entry.filename)
                        collected.append((entry_name, io.BytesIO(data), entry.file_size))
            print(f"[Zip] Extracted {len(collected)} file(s) from {path.name}")
        elif path.suffix.lower() in extensions and path.name not in exclude_names:
            collected.append((path.name, path, path.stat().st_size))
    else:
        for f in path.rglob("*"):
            if f.is_file():
                if f.suffix.lower() == ".zip":
                    #Handle zip files found within directory
                    print(f"[Zip] Opening archive: {f.name}")
                    before = len(collected)
                    with zipfile.ZipFile(f, "r") as zf:
                        for entry in zf.infolist():
                            entry_name = Path(entry.filename).name
                            entry_ext  = Path(entry.filename).suffix.lower()
                            if entry_ext in extensions and entry_name not in exclude_names:
                                data = zf.read(entry.filename)
                                collected.append((entry_name, io.BytesIO(data), entry.file_size))
                    print(f"[Zip] Extracted {len(collected) - before} file(s) from {f.name}")
                elif f.suffix.lower() in extensions and f.name not in exclude_names:
                    collected.append((f.name, f, f.stat().st_size))

    return collected


def parse_documents(path: str) -> list[dict]:

    #Extracts text and embedded images from PDF and DOCX files
    #Sends extracted text to OpenAI GPT in chunks of 12,000 characters
    #Sums word counts across all chunks and adds embedded image CO2e to text CO2e

    target = Path(path)
    files  = collect_files_from_path(target, document_extensions, exclude_names={"_chat.txt"})

    system = """
    You are a data extraction assistant.
    Count the total number of words in this document chunk.
    Exclude page numbers. Include all other content including headings, footers, captions, and reference lists.
    Once you have a count, review it: does it seem accurate given the file size and amount of text provided? If not, revise it.
    RESPOND ONLY WITH A JSON OBJECT: {"word_count": <integer>}
"""

    results = []
    for name, source, size_bytes in files:
        print(f"\n[Document] Reading: {name}")

        #Handle BytesIO from zip
        #write to temp file for pypdf/fitz compatibility

        if isinstance(source, io.BytesIO):
            import tempfile
            suffix = Path(name).suffix.lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(source.read())
                tmp_path = Path(tmp.name)
            try:
                text, embedded_co2e = extract_text_and_images(tmp_path)
            finally:
                tmp_path.unlink(missing_ok=True)
        else:
            text, embedded_co2e = extract_text_and_images(source)

        if not text.strip():
            print(f"[Document] No text extracted from {name}. Skipping.")
            continue

        #Split full document text into 12,000-character chunks
        chunk_size = 12000
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
        print(f"[Document] {name}: {len(chunks)} chunk(s) to process...")

        total_wc = 0
        for chunk_idx, chunk in enumerate(chunks):
            response = ask_gpt(system, chunk)
            try:
                chunk_wc = int(json.loads(response)["word_count"])
            except Exception as e:
                print(f"[Document] GPT word count failed on chunk {chunk_idx + 1} ({e}). Using split count.")
                chunk_wc = len(chunk.split())
            print(f"[Document] Chunk {chunk_idx + 1}/{len(chunks)}: {chunk_wc} words")
            total_wc += chunk_wc

        #Combine text CO2e and embedded image CO2e into total CO2e
        text_co2e  = document_text_co2e(total_wc)
        total_co2e = text_co2e + embedded_co2e
        print(f"[Document] {name}: {total_wc} total words = {text_co2e:.4f}g CO2e (text) + {embedded_co2e:.6f}g CO2e (images) = {total_co2e:.4f}g CO2e")
        results.append({
            "type": "Document",
            "source": name,
            "message_count": None,
            "word_count": total_wc,
            "duration_seconds": None,
            "recipients": None,
            "file_size_bytes": size_bytes,
            "co2e_g": total_co2e,
        })

    return results


#Image Parser
image_extensions = {".jpg", ".jpeg", ".webp"}

def parse_images(path: str) -> list[dict]:

    #Calculate CO2e for standalone images

    target = Path(path)
    files  = collect_files_from_path(target, image_extensions)

    results = []
    for name, source, size_bytes in files:
        size_kb = size_bytes / 1024
        co2e    = image_co2e(size_bytes)
        print(f"[Image] {name}: {size_kb:.1f} KB = {co2e:.6f}g CO2e")
        results.append({
            "type": "Image",
            "source": name,
            "message_count": None,
            "word_count": None,
            "duration_seconds": None,
            "recipients": None,
            "file_size_bytes": size_bytes,
            "co2e_g": co2e,
        })

    return results


#Audio and Video Parser
audio_video_extensions = {".opus", ".mp4"}

def get_duration_seconds(filepath: Path) -> float:

    #Extract the duration in seconds of audio or video files using ffprobe.
    import subprocess
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                str(filepath)
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        duration = float(result.stdout.strip())
        return duration
    except Exception as e:
        print(f"[Media] Could not extract duration from {filepath.name}: {e}")
        return 0.0


def media_co2e(duration_seconds: float, num_recipients: int) -> float:
    
    #Calculate the CO2e for audio or video files.
    filming_hours = duration_seconds / 3600
    playback_hours = (duration_seconds / 3600) * num_recipients
    total_hours = filming_hours + playback_hours
    return time_based_co2e(total_hours)


def parse_media(path: str, num_recipients: int = 4) -> list[dict]:

    target = Path(path)
    files  = collect_files_from_path(target, audio_video_extensions)

    results = []
    for name, source, size_bytes in files:

        #Write BytesIO from zip to temp file for ffprobe compatibility
        if isinstance(source, io.BytesIO):
            import tempfile
            suffix = Path(name).suffix.lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(source.read())
                tmp_path = Path(tmp.name)
            try:
                duration_seconds = get_duration_seconds(tmp_path)
            finally:
                tmp_path.unlink(missing_ok=True)
        else:
            duration_seconds = get_duration_seconds(source)

        if duration_seconds == 0.0:
            print(f"[Media] {name}: duration could not be determined. Skipping.")
            continue

        duration_str = f"{int(duration_seconds // 60)}m {int(duration_seconds % 60)}s"
        co2e = media_co2e(duration_seconds, num_recipients)
        media_type = "Video" if Path(name).suffix.lower() == ".mp4" else "Audio"
        print(f"[Media] {name} ({media_type}): {duration_str} x {num_recipients} recipients = {co2e:.6f}g CO2e")

        results.append({
            "type": media_type,
            "source": name,
            "message_count": None,
            "word_count": None,
            "duration_seconds": round(duration_seconds, 2),
            "recipients": num_recipients,
            "file_size_bytes": size_bytes,
            "co2e_g": co2e,
        })

    return results

    return results


# CSV Report log
def write_report(results: list[dict], output_path: str):
    #Write a CSV report and print a summary to the terminal.

    fieldnames = ["type", "source", "message_count", "word_count", "duration_seconds", "recipients", "file_size_bytes", "co2e_g"]

    total_co2e = sum(r["co2e_g"] for r in results)
    breakdown  = {}
    for r in results:
        t = r["type"]
        breakdown[t] = breakdown.get(t, 0) + r["co2e_g"]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval= "")
        writer.writeheader()
        writer.writerows(results)

        writer.writerow({k: "" for k in fieldnames})

        #Subtotal rows
        for artefact_type, subtotal in sorted(breakdown.items()):
            writer.writerow({
                "type": f"Subtotal ({artefact_type})",
                "co2e_g": round(subtotal, 6),
            })

        #Total CO2e row
        writer.writerow({
            "type": "Total",
            "source": "All artefacts",
            "co2e_g": round(total_co2e, 6),
        })

        #Total CO2e in kg
        writer.writerow({
            "type": "Total (kg)",
            "source": "All artefacts",
            "co2e_g": round(total_co2e / 1000, 9),
        })

    print("\n")
    print("Carbon Footprint Summary \n")
    for artefact_type, subtotal in sorted(breakdown.items()):
        print(f" {artefact_type:<20} {subtotal:>10.4f} g CO2e")
    print("\n")
    print(f" {'TOTAL':<20} {total_co2e:>10.4f} g CO2e")
    print(f" {'':20} {total_co2e/1000:>10.6f} kg CO2e")
    print("\n")
    print(f"\n Results saved to {output_path}")


# OpenAI GPT Analysis & Recommendations
def analyse_results(results: list[dict], output_path: str):
    
    #This sends the projects CO2e results to the LLM to analyse, identify inefficiencies and recommend sustainable insights
    #The recommendations are printed to the terminal and appended to the results.CSV

    #Compile CO2e summary for the LLM
    docs = [r for r in results if r["type"] == "Document"]
    emails = [r for r in results if r["type"] == "Email"]
    images = [r for r in results if r["type"] == "Image"]
    whatsapp = [r for r in results if r["type"] == "WhatsApp"]
    audio    = [r for r in results if r["type"] == "Audio"]
    video    = [r for r in results if r["type"] == "Video"]
    total_co2e = sum(float(r["co2e_g"]) for r in results)

    #Detect duplicate files
    doc_names = [r["source"] for r in docs]
    duplicates = [name for name in set(doc_names) if doc_names.count(name) > 1]

    #Find top CO2e emitters
    sorted_docs = sorted(docs, key=lambda r: float(r["co2e_g"]), reverse=True)
    top_docs = [(r["source"], round(float(r["co2e_g"]), 4)) for r in sorted_docs[:5]]

    #Zero word emails
    zero_emails = [r["source"] for r in emails if int(r.get("word_count") or 0) == 0]

    summary = f"""
You are analysing the digital carbon footprint of a student project.
Below is a structured summary of the CO2e results. Based on this data, identify inefficiencies
and recommend specific streamlining procedures the team could adopt to reduce their digital
carbon footprint in future projects.

Total CO2e: {total_co2e:.4f}g

Artefact breakdown:
    WhatsApp messages: {whatsapp[0]["message_count"] if whatsapp else 0} messages = {sum(float(r["co2e_g"]) for r in whatsapp):.4f}g CO2e
    Emails: {len(emails)} emails = {sum(float(r["co2e_g"]) for r in emails):.4f}g CO2e
    Documents: {len(docs)} files = {sum(float(r["co2e_g"]) for r in docs):.4f}g CO2e
    Images: {len(images)} images = {sum(float(r["co2e_g"]) for r in images):.4f}g CO2e
    Audio recordings: {len(audio)} files = {sum(float(r["co2e_g"]) for r in audio):.4f}g CO2e
    Video recordings: {len(video)} files = {sum(float(r["co2e_g"]) for r in video):.4f}g CO2e

Duplicate document files detected:
{duplicates if duplicates else "None detected"}

Top 5 CO2e emmitting documents:
{top_docs}

Zero-word emails (emails with no body text):
{zero_emails if zero_emails else "None"}

All document sources, word counts and associated CO2e:
{[(r["source"], r.get("word_count", 0), round(float(r["co2e_g"]), 4)) for r in docs]}

Please provide:
1. Specific observations about inefficiencies in this workflow
2. Concrete recommendations for reducing digital carbon emissions in future projects e.g. how could any knowledge/files be reused
3. A note on any duplicate files and what they suggest about the team's file management practices
Keep your response concise and practical. Use plain text with no markdown formatting, no bullet points, and no dashes or hyphens at the start of sentences. Write in full paragraphs only.
"""

    system = """
    You are a digital sustainability analyst specialising in reducing the carbon footprint of student project workflows.
    Based on the data provided, identify inefficiencies and recommend streamlining procedures. 
    Once you have drafted your recommendations, review them:
        are they specific to this dataset or generic?
        Are they evidence-based?
        Do they provide practical steps to follow?

    Revise any that are too vague before returning.
    Use plain text with no bullet points or dashes.

"""

    response = ask_gpt(system, summary, model="gpt-4o-mini")

    print("\n Carbon Analysis & Recommendation \n")
    print("\n" + response)

    #Add GPT recommendations to results.CSV
    fieldnames = ["type", "source", "message_count", "word_count", "recipients", "file_size_bytes", "co2e_g"]
    with open(output_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval= "")


        writer.writerow({k: "" for k in fieldnames})

        #GPT recommendations header row
        writer.writerow({
            "type": "Carbon Analysis & Recommendation",
        })

        #Write each line of the GPT response as a separate row under source column
        for line in response.splitlines():
            if line.strip():
                writer.writerow({
                    "type": line.strip(),
                })

    print(f"\n Carbon recommendations added to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Calculate CO2e for Digital Workflows Across Student Projects"
    )
    parser.add_argument("--whatsapp", help="Path to exported WhatsApp _chat.txt file")
    parser.add_argument("--emails", help="Path to email thread .txt file or folder")
    parser.add_argument("--docs", nargs="+", help="One or more paths to document files or folders (pdf, docx, etc.)")
    parser.add_argument("--images", nargs="+", help="One or more paths to image files or folders (jpg, png, etc.)")
    parser.add_argument("--media", nargs="+", help="One or more paths to audio/video files or folders (opus, mp4, etc.)")
    parser.add_argument("--recipients", type=int, default=4, help="Number of recipients for audio/video playback (default: 4)")
    parser.add_argument("--output", default="carbon_results.csv", help="Output CSV path")
    args = parser.parse_args()

    if not any([args.whatsapp, args.emails, args.docs, args.images, args.media]):
        print("No inputs provided. Use --help for usage instructions.")
        return

    all_results = []

    if args.whatsapp:
        all_results.append(parse_whatsapp(args.whatsapp))

    if args.emails:
        email_path = Path(args.emails)
        if email_path.is_dir():
            for txt_file in email_path.glob("*.txt"):
                all_results.extend(parse_emails(str(txt_file)))
        else:
            all_results.extend(parse_emails(args.emails))

    if args.docs:
        for doc_path in args.docs:
            all_results.extend(parse_documents(doc_path))

    if args.images:
        for img_path in args.images:
            all_results.extend(parse_images(img_path))

    if args.media:
        for media_path in args.media:
            all_results.extend(parse_media(media_path, num_recipients=args.recipients))

    if all_results:
        write_report(all_results, args.output)
        analyse_results(all_results, args.output)
    else:
        print("No artefacts processed. Check your input paths.")


if __name__ == "__main__":
    main()
