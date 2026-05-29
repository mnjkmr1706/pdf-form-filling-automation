#!/usr/bin/env python3
"""
Parse PDF using Azure Document Intelligence - prebuilt_document model
"""
import os
from pathlib import Path
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential

def parse_pdf_with_azure(pdf_path: str) -> dict:
    """
    Parse PDF file using Azure Document Intelligence prebuilt_document model.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        Dictionary containing parsed document content (doc_context)
    """
    # Get Azure credentials from environment variables
    endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
    key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
    
    if not endpoint or not key:
        raise ValueError(
            "Please set AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and "
            "AZURE_DOCUMENT_INTELLIGENCE_KEY environment variables"
        )
    
    # Initialize the client
    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    
    # Read the PDF file
    with open(pdf_path, "rb") as pdf_file:
        poller = client.begin_analyze_document(
            model_id="prebuilt-document",
            analyze_request=pdf_file,
        )
    
    # Get the results
    result = poller.result()
    
    # Structure the response as doc_context
    doc_context = {
        "status": "success",
        "model": "prebuilt-document",
        "file": Path(pdf_path).name,
        "content": result.content,
        "pages": result.pages,
        "tables": result.tables if hasattr(result, 'tables') else [],
        "paragraphs": result.paragraphs if hasattr(result, 'paragraphs') else [],
        "key_value_pairs": result.key_value_pairs if hasattr(result, 'key_value_pairs') else [],
        "raw_result": result,
    }
    
    return doc_context


if __name__ == "__main__":
    # Example usage
    pdf_file = "Claims Test Forms/AETNA_Form1.pdf"
    
    try:
        doc_context = parse_pdf_with_azure(pdf_file)
        print(f"✓ Successfully parsed: {doc_context['file']}")
        print(f"  Content length: {len(doc_context['content'])} characters")
        print(f"  Pages: {len(doc_context['pages'])}")
        print(f"  Tables: {len(doc_context['tables'])}")
        print("\nExtracted content (first 500 chars):")
        print(doc_context['content'][:500])
        
    except FileNotFoundError:
        print(f"✗ File not found: {pdf_file}")
    except ValueError as e:
        print(f"✗ Configuration error: {e}")
    except Exception as e:
        print(f"✗ Error parsing document: {e}")
