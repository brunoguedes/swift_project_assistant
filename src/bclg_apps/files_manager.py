import os
import shutil

class FilesManager:
    def relative_file_path(self, base_path, file_path):
        # Normalize the base path to remove any trailing slashes
        normalized_base_path = os.path.normpath(base_path)
        # Remove the base path from the file path
        relative_file_path = file_path.replace(normalized_base_path + os.sep, '')
        return relative_file_path

    def list_files(self, base_path="./", exclude=None):
        if exclude is None:
            exclude = []

        result = []

        for root, dirs, files in os.walk(base_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__" and d not in exclude]

            filtered_items = [item for item in files if not any(ignore in item for ignore in [".DS_Store", ".gitignore"])]
            for file in filtered_items:
                if file not in exclude:
                    relative_path = os.path.relpath(os.path.join(root, file), base_path)
                    result.append(relative_path)

            for dir in dirs:
                if dir not in exclude:
                    relative_path = os.path.relpath(os.path.join(root, dir), base_path) + "/"
                    result.append(relative_path)

        return sorted(result)

    def format_file_tree(self, file_list):
        tree = {}
        for path in file_list:
            parts = path.split("/")
            current = tree
            for part in parts:
                if part != "":
                    if part not in current:
                        current[part] = {}
                    current = current[part]

        def print_tree(node, prefix="", is_last=True):
            tree_structure = ""
            if isinstance(node, dict):
                keys = list(node.keys())
                for i, key in enumerate(keys):
                    is_last_child = i == len(keys) - 1
                    tree_structure += prefix + ("└── " if is_last_child else "├── ") + key + "\n"
                    tree_structure += print_tree(node[key], prefix + ("    " if is_last_child else "│   "), is_last_child)
            return tree_structure

        return print_tree(tree).strip()
    
    def read_file_content(self, base_path, file):
        file_path = os.path.join(base_path, file)
        content = ""
        if os.path.isfile(file_path):
            with open(file_path, 'r') as f:
                content = f.read()
        return content

    def list_files_by_type(self, base_path, file_types, exclude_folders=None):
        if exclude_folders is None:
            exclude_folders = []

        result = []
        for root, dirs, files in os.walk(base_path):
            dirs[:] = [d for d in dirs if not any(os.path.abspath(os.path.join(root, d)).startswith(os.path.abspath(os.path.join(base_path, exclude))) for exclude in exclude_folders)]

            for file in files:
                if any(file.endswith(file_type) for file_type in file_types):
                    relative_path = os.path.relpath(os.path.join(root, file), base_path)
                    result.append(relative_path)
        return sorted(result)
    def delete_folder(self, folder_path):
        """
        Delete a folder and all its contents.

        Args:
            folder_path (str): The path to the folder to be deleted.

        Returns:
            bool: True if the folder was successfully deleted, False otherwise.
        """
        try:
            if os.path.exists(folder_path):
                shutil.rmtree(folder_path)
                return True
            else:
                print(f"The folder {folder_path} does not exist.")
                return False
        except Exception as e:
            print(f"An error occurred while trying to delete the folder {folder_path}: {str(e)}")
            return False
