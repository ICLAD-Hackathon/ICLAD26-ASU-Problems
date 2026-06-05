# Dependencies

## System Tools

| Tool | Version | Purpose |
|---|---:|---|
| Python | 3.10 or newer | Runner, starter agent, and evaluator scripts |
| KLayout | 0.30.1 | Rendering repaired layout scripts and running ASAP7 DRC |

Verify KLayout:

```bash
klayout -v
```

Expected output:

```text
KLayout 0.30.1
```

## Python Packages

Install Python packages from the repository root:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

The Python dependencies are listed in `requirements.txt`.

## Vertex AI Express Mode

The benchmark model service uses Vertex AI Express Mode through the Google GenAI SDK. Set the API key in the shell that runs the benchmark:

```bash
export EXPRESS_MODE_KEY="your_actual_api_key_here"
```
