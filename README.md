# AI SRE Agent

Autonomous AI agent that analyzes Kubernetes logs, identifies root causes using RAG over GitHub source code, and generates remediation pull requests using LLMs to automate production incident response end to end.

## Overview

This project integrates:
- **Kubernetes API** — for log retrieval and cluster state inspection
- **GitHub API** — for source code retrieval and pull request creation
- **Retrieval-Augmented Generation (RAG)** — to ground LLM responses in actual codebase context
- **Large Language Models** — for root-cause analysis and remediation suggestion generation

## Architecture

1. **Log Analyzer** — Parses Kubernetes pod/deployment logs to detect anomalies and error patterns
2. **Root Cause Engine** — Uses RAG to retrieve relevant source code from GitHub repos and correlate with log errors
3. **Remediation Generator** — LLM-powered generation of code fixes and configuration changes
4. **PR Creator** — Automatically creates pull requests with suggested fixes

## Tech Stack

- Python, LangChain
- Kubernetes (client-go / kubectl)
- GitHub REST API
- Vector database for RAG embeddings
- LLM integration for code analysis and fix generation

## Status

Active development.
