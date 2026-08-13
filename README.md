# AI-Carbon-Calculator

# AI Carbon Calculator

A Python tool that measures the carbon footprint of everyday digital communication, at the level of individual emails, messages, and files, rather than just at the organisation level (which is as far as most existing tools, including Microsoft's and Google's carbon dashboards, currently go).

Built as my final year dissertation project (BSc Computer Science with Artificial Intelligence, Northumbria University).

## What It Does

The calculator takes a folder or export of digital artefacts from a project, WhatsApp chat logs, email threads, documents, images, audio, and video, and estimates the CO2e (carbon dioxide equivalent) emissions each one generated, based on the time or data volume involved. It uses GPT-4o mini to extract word counts from documents and emails, and to generate a written analysis of inefficiencies and recommendations at the end of each run.

## Usage

```bash
pip install -r requirements.txt

python carbon_calculator_teaching.py \
  --whatsapp path/to/_chat.txt \
  --emails path/to/emails/ \
  --docs path/to/documents/ \
  --images path/to/images/ \
  --media path/to/audio_video/ \
  --recipients 4 \
  --output results.csv
```

Any combination of `--whatsapp`, `--emails`, `--docs`, `--images`, and `--media` can be supplied; only the artefact types provided are processed. Folders and zip archives are both supported as input paths.

**Prerequisites:** an OpenAI API key set as the `OPENAI_API_KEY` environment variable, and `ffmpeg`/`ffprobe` installed and available on your system PATH (used for extracting audio/video duration).

## The Four Versions

This repo contains four functionally identical versions of the calculator. Each uses the same calculation logic and file handling; the only difference is the wording of the prompts sent to the LLM for word counting, message counting, and workflow analysis. They were built to run a controlled comparison of four prompt engineering techniques.

| Version | Technique | Result |
|---|---|---|
| `carbon_calculator_baseline.py` | Direct instruction + output format | Inconsistent: missed emails on some runs (as few as 17 of 23 detected) |
| `carbon_calculator_chain_of_thought.py` | Explicit numbered reasoning steps | Introduced its own inconsistency (WhatsApp count varied 625–805 across runs with no change in input) |
| `carbon_calculator_reflection.py` | Self-review step before returning an answer | Tended to inflate word counts (document CO2e ~20% higher than baseline) |
| `carbon_calculator_teaching.py` | Worked example (few-shot) included in the prompt | **Most consistent**: the only version to reliably detect all 23 emails across repeated runs |

If you only want to look at one file, start with `carbon_calculator_teaching.py`, it's the strongest performer. `carbon_calculator_baseline.py` is the simplest read if you just want to understand the core pipeline structure first.

## A Bug Worth Mentioning

Early runs produced severely undercounted word counts on long documents. The cause: sending a full document as a single request to the model was hitting a practical limit on reliable extraction. The fix splits documents into 12,000-character chunks before processing, which corrected one document's word count from 1,554 to 9,585, a 113.7% increase, and generalised across the rest of the dataset.

## Results

Applied to a real student group project's digital footprint over one semester:

- **Total footprint: 1.042kg CO2e**
- WhatsApp messages accounted for 61.83% of emissions, despite each message costing very little individually, reflecting sheer message volume
- Documents were the second-largest contributor (37.09%), driven by a small number of long iterative reports
- Images, audio, and video together contributed under 0.5%

## Limitations

- **Non-determinism:** identical inputs can produce different outputs across runs, since the calculator depends on an LLM for word and message counting. Results should be read as estimates within a range, not exact measurements.
- **Operational emissions only:** embodied carbon (device manufacturing) is excluded, since it would require device-specific data not available for this study. The true footprint is materially higher than the reported figure.
- **Single dataset:** the calculator has only been validated against one project's data; the methodology is designed to generalise, but this hasn't yet been tested across other contexts.

## Requirements

See `requirements.txt`. Key dependencies: `openai`, `pymupdf`, `pypdf`, `python-docx`. `ffmpeg` must be installed separately as a system binary (not via pip).
