# Construction POC

This repository contains a full-stack proof-of-concept application for document processing and analysis in the construction domain. The stack consists of a Python FastAPI backend leveraging AI agents and a React frontend built with Vite.

## Project Structure

- **Backend** (`/`): A Python FastAPI server providing endpoints for processing documents, utilizing `pydantic-ai` and local AI agents.
- **Frontend** (`/demo_ui`): A React frontend application strictly typed with TypeScript and bundled using Vite.
- **Scripts** (`/scripts`): Various Python scripts used for pipeline execution, extraction, computer vision processing (e.g., rasterizing, path extraction), and data processing.

## ⚡ Quick Start commands

To quickly spin up both servers, open **two separate terminals**:

**Terminal 1 (Backend):**
```bash
# Windows
.\.venv\Scripts\Activate.ps1 && python api.py

# macOS/Linux
source .venv/bin/activate && python api.py
```

**Terminal 2 (Frontend):**
```bash
cd demo_ui
npm run dev
```

## Prerequisites

Make sure you have the following installed on your system:
- **Python**: 3.10 or newer
- **Node.js**: v18 or newer
- **npm** (comes with Node.js)

---

## 🚀 Getting Started

Follow the steps below to start both the backend server and the frontend application locally.

### 1. Backend Setup

The backend runs on FastAPI.

1. **Navigate to the project root:**
   ```bash
   cd construction_poc
   ```

2. **Create and activate a virtual environment:**
   - **Windows:**
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\Activate.ps1
     ```
   - **macOS/Linux:**
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Variables:**
   Make sure you specify any required environment variables (like API keys for your AI models) inside an `.env` file or export them directly in your terminal if applicable.

5. **Run the backend server:**
   ```bash
   python api.py
   ```
   *The FastAPI server will start, typically exposing the API on `http://127.0.0.1:8000`.*

### 2. Frontend Setup

The frontend is a Vite + React application.

1. **Navigate to the frontend directory:**
   Open a **new terminal window/tab**, and go to the UI folder:
   ```bash
   cd construction_poc/demo_ui
   ```

2. **Install dependencies:**
   ```bash
   npm install
   ```

3. **Start the development server:**
   ```bash
   npm run dev
   ```
   *Vite will start the development server, typically accessible at `http://localhost:5173`.*

---

## 🛠️ Additional Resources & Useful Commands

### Backend
- **Scripts & Extraction pipelines:** Check the `scripts/` directory for individual pipeline components. These can typically be executed directly (e.g., `python scripts/step18_highlight_overlay.py`).
- **Interactive API Documentation:** Once the backend is running, you can access the interactive Swagger UI by navigating to:
  `http://127.0.0.1:8000/docs`

### Frontend
- **Build for Production:**
  ```bash
  npm run build
  ```
- **Preview Production Build locally:**
  ```bash
  npm run preview
  ```

## Overview of Technologies Used
- **Backend:** Python, FastAPI, Pydantic, PydanticAI
- **Frontend:** React 19, TypeScript, Vite, TailwindCSS (for styling)
- **Data processing:** Heavy usage of python image/computer-vision processing pipelines (`skimage`, etc.) located in `/scripts`.
