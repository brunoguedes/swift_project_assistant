import os
from dotenv import load_dotenv
from pathlib import Path
import streamlit as st

from bclg_apps.files_manager import FilesManager
from bclg_apps.llms import LLMs
import swift_dependency_analysis as sda
import prompt_generator as prompt_generator
import json

from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain

class App:

    def run(self):
        load_dotenv()
        is_local = os.getenv("IS_LOCAL", "false").lower() == "true"
        st.title('Swift Project Assistant')

        # LLM Picker
        llms = LLMs()
        chosen_llm = st.selectbox(
            "Please select the model you'd like to use:",
            llms.get_available_llms(model_type=None if is_local else 'remote'),
            index=0,
        )
        llm = llms.get_llm(chosen_llm)

        file_types = st.text_input("Enter the file types you'd like to summarize (comma-separated)", value="swift").split(',')
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
        if st.button('Generate'):
            for item in files:
                file_path = os.path.join(base_path, item)
                analysis_result = sda.analyze_file(file_path)
                analysis_results.append(analysis_result)
            st.session_state.analysis_results = analysis_results

        if 'analysis_results' in st.session_state:
            analysis_results = st.session_state.analysis_results

            if st.button('Generate RAG Data'):
                for item in analysis_results:
                    file_path = item["file"]
                    summary = prompt_generator.generate_summary_prompt(llm, item)
                    
                    # Change the file extension to .md and save under "documentation" folder
                    md_file_path = Path(f"{base_path}/documentation") / Path(file_path).with_suffix('.md').name
                    
                    # Create necessary directories if they don't exist
                    md_file_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Write the summary content to the markdown file
                    with open(md_file_path, 'w') as md_file:
                        md_file.write(summary)
                    st.success(f"Summary saved to {md_file_path}")
                    
            # File picker
            file_picker = st.selectbox('Select a file to inspect', options=[result['file'] for result in analysis_results])
            if file_picker:
                selected_file_details = next(item for item in analysis_results if item["file"] == file_picker)
                if st.button('Generate Summary'):
                    summary = prompt_generator.generate_summary_prompt(llm, selected_file_details)
                    st.session_state.summary = summary
                    st.session_state.display_option = "Markdown"
                
                if 'summary' in st.session_state:
                    summary = st.session_state.summary
                    display_option = st.radio("Choose display format:", ["Markdown", "Code"], index=0 if st.session_state.display_option == "Markdown" else 1, key='display_option')
                    
                    if display_option == "Markdown":
                        st.markdown(summary)
                    else:
                        st.code(summary, language='markdown')

if __name__ == "__main__":
    load_dotenv()
    app = App()
    app.run()