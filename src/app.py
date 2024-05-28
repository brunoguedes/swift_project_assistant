import os
from dotenv import load_dotenv

import streamlit as st

from bclg_apps.files_manager import FilesManager
from bclg_apps.llms import LLMs
import swift_dependency_analysis as sda
import json

from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain

class App:

    def run(self):
        load_dotenv()
        is_local = os.getenv("IS_LOCAL", "false").lower() == "true"
        st.title('Project Summarization Assistant')

        # LLM Picker
        llms = LLMs()
        chosen_llm = st.selectbox(
            "Please select the model you'd like to use:",
            llms.get_available_llms(model_type=None if is_local else 'remote'),
            index=0,
        )
        llm = llms.get_llm(chosen_llm)

        file_types = st.text_input("Enter the file types you'd like to summarize (comma-separated)", value="swift,storyboard").split(',')
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

            # File picker
            file_picker = st.selectbox('Select a file to inspect', options=[result['file'] for result in analysis_results])
            if file_picker:
                selected_file_details = next(item for item in analysis_results if item["file"] == file_picker)
                types = [detail['type'] for detail in selected_file_details['details']]

                # Type picker
                type_picker = st.selectbox('Select a type to inspect', options=types)
                if type_picker:
                    selected_type_details = next(detail for detail in selected_file_details['details'] if detail['type'] == type_picker)
                    methods = selected_type_details['methods']

                    # Method picker
                    method_picker = st.selectbox('Select a method to get its implementation', options=methods)
                    if method_picker:
                        if st.button('Find Implementation'):
                            method_implementation = sda.get_method_implementation(file_picker, selected_file_details['structure'], method_picker)
                            if method_implementation:
                                st.subheader(f'Method Implementation for {method_picker}')
                                st.code(method_implementation, language='swift')
                            else:
                                st.write(f'Method {method_picker} not found.')

if __name__ == "__main__":
    load_dotenv()
    app = App()
    app.run()