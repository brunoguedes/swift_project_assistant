import os
from dotenv import load_dotenv
from pathlib import Path
import streamlit as st

from bclg_apps.files_manager import FilesManager
from bclg_apps.llms import LLMs
from bclg_apps.rag import RAG
import swift_dependency_analysis as sda
import llm_runner as llm_runner
import json

from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain

class App:

    def run(self):
        load_dotenv()
        is_local = os.getenv("IS_LOCAL", "false").lower() == "true"
        programming_language = "Swift"
        embedding_type = "openai"
        st.title(body=f"{programming_language} Project Assistant")

        # LLM Picker
        llms = LLMs()
        chosen_llm = st.selectbox(
            "Please select the model you'd like to use:",
            llms.get_available_llms(model_type=None if is_local else 'remote'),
            index=0,
        )
        llm = llms.get_llm(chosen_llm)

        file_types = st.text_input("Enter the file types you'd like to summarize (comma-separated)", value=programming_language.lower()).split(',')
        exclude_folders = st.text_area('Folders to exclude (comma separated)', value='.git,.DS_Store,Pods').split(',')

        # Clean up the exclude_folders list
        exclude_folders = [folder.strip() for folder in exclude_folders if folder.strip()]
        base_path = st.text_input('Enter the folder you want to generate the Summaries', value='../SpaceX')

        st.subheader('Folder Structure:')
        fm = FilesManager()
        files = fm.list_files_by_type(base_path=base_path, file_types=file_types, exclude_folders=exclude_folders)
        project_structure = fm.format_file_tree(file_list=files)
        st.code(project_structure)
        st.divider()

        analysis_results = []
        for item in files:
            file_path = os.path.join(base_path, item)
            analysis_result = sda.analyze_file(file_path)
            analysis_results.append(analysis_result)
        st.session_state.analysis_results = analysis_results

        if 'analysis_results' in st.session_state:
            analysis_results = st.session_state.analysis_results

            # File picker
            file_picker = st.selectbox('Select a file to inspect', options=[result['file'] for result in analysis_results])
            if file_picker:
                selected_file_details = next(item for item in analysis_results if item["file"] == file_picker)
                if st.button('Generate Summary'):
                    summary = f"File: {fm.relative_file_path(base_path=base_path, file_path=selected_file_details.get('file', 'Unknown file'))}\n\n"
                    summary += llm_runner.generate_code_summary(llm, programming_language, base_path, selected_file_details)
                    # Count the number of words in the summary
                    word_count = len(summary.split())
                    print(f"Number of words in summary: {word_count}")
                    st.session_state.summary = summary
                    st.session_state.display_option = "Markdown"
                
                if 'summary' in st.session_state:
                    summary = st.session_state.summary
                    display_option = st.radio("Choose display format:", ["Markdown", "Code"], index=0 if st.session_state.display_option == "Markdown" else 1, key='display_option')
                    
                    if display_option == "Markdown":
                        st.markdown(summary)
                    else:
                        st.code(summary, language='markdown')

            vector_db_path = Path(f"{base_path}/rag/")
            if st.button('Generate RAG Data'):
                fm.delete_folder(vector_db_path)
                vector_db_path.mkdir(parents=True, exist_ok=True)
                rag = RAG(vector_db_path=vector_db_path, embedding_type=embedding_type)
                for item in analysis_results:
                    file_path = item["file"]
                    # Change the file extension to .md and save under "documentation" folder
                    md_file_path = Path(f"{base_path}/documentation") / Path(file_path).with_suffix('.md').name

                    # Check if the markdown file already exists
                    if not md_file_path.exists():
                        # Generate the summary if the file does not exist
                        summary = f"File: {fm.relative_file_path(base_path=base_path, file_path=item.get('file', 'Unknown file'))}\n\n"
                        summary += llm_runner.generate_code_summary(llm, programming_language, base_path, item)

                        # Create necessary directories if they don't exist
                        md_file_path.parent.mkdir(parents=True, exist_ok=True)

                        # Write the summary content to the markdown file
                        with open(md_file_path, 'w') as md_file:
                            md_file.write(summary)
                        st.success(f"Summary saved to {md_file_path}")
                    else:
                        st.info(f"Summary already exists for {md_file_path}")
                    # Create embeddings and store them in the vector database
                    rag.create_embeddings_and_store(md_file_path)
            threshold = st.slider("Threshold Score", min_value=0.01, max_value=0.3, value=0.15)
            if os.path.exists(vector_db_path):
                query = st.text_input("What would you like to know?")
                if st.button('Ask'):
                    rag = RAG(vector_db_path=vector_db_path, embedding_type=embedding_type)
                    results = rag.retrieve_embeddings(query, n=10, threshold=threshold)
                    # Combine the retrieved results into a context for the LLM
                    context = "\n\n".join([result.page_content for result in results])  # Access page_content attribute

                    # Use the context to generate the final answer
                    chat_prompt = f"""
                        You are a coding assistant specialized in answering questions about an iOS app project using the {programming_language} programming language. Bellow are the context and question.
                        
                        <context>
                        {context}
                        </context>
                        
                        <question>
                        {query}
                        </question>
                        
                        Based on the context you were given, please answer the question."""

                    result = llm.invoke(chat_prompt)
                    summary = ""
                    if isinstance(result, str):
                        summary = result
                    else:
                        summary = result.content
                    st.write(summary)

if __name__ == "__main__":
    load_dotenv()
    app = App()
    app.run()
