import subprocess
import json

def run_sourcekitten(file_path):
    result = subprocess.run(['sourcekitten', 'structure', '--file', file_path], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running SourceKitten: {result.stderr}")
        return {}
    return json.loads(result.stdout)

def extract_provides(structure):
    provides = []
    for item in structure.get('key.substructure', []):
        if item['key.kind'] in [
            'source.lang.swift.decl.struct',
            'source.lang.swift.decl.class',
            'source.lang.swift.decl.enum',
            'source.lang.swift.decl.protocol',
            'source.lang.swift.decl.function.method.instance',
            'source.lang.swift.decl.var.global'
        ]:
            provides.append(item['key.name'])
    return provides

def extract_depends_per_item(item):
    depends = []
    
    def extract_from_substructure(substructure):
        for sub_item in substructure:
            if sub_item.get('key.kind') in [
                'source.lang.swift.expr.call',
                'source.lang.swift.decl.var.instance',
                'source.lang.swift.decl.var.local',
                'source.lang.swift.decl.function.free',
                'source.lang.swift.decl.function.method.static',
                'source.lang.swift.decl.function.method.class',
                'source.lang.swift.decl.function.method.instance'
            ]:
                typename = sub_item.get('key.typename')
                if typename:
                    depends.append(typename)
            if 'key.substructure' in sub_item:
                extract_from_substructure(sub_item['key.substructure'])
    
    extract_from_substructure(item.get('key.substructure', []))
    
    return list(set(depends))

def parse_instance_variable(var_json):
    """
    Parse a JSON object representing an instance variable and return its declaration.

    Parameters:
    - var_json (dict): The JSON object representing the instance variable.

    Returns:
    - str: The instance variable declaration.
    """
    if var_json.get("key.kind") != "source.lang.swift.decl.var.instance":
        return ""

    var_name = var_json.get("key.name", "unknown_var")
    var_type = var_json.get("key.typename", None)

    var_declaration = f"{var_name}"
    if var_type:
        var_declaration = f"{var_name}: {var_type}"
    
    return var_declaration

def parse_instance_method(method_json):
    """
    Parse a JSON object representing an instance method and return its signature.

    Parameters:
    - method_json (dict): The JSON object representing the method.

    Returns:
    - str: The method signature.
    """
    if method_json.get("key.kind") != "source.lang.swift.decl.function.method.instance":
        return ""

    method_name = method_json.get("key.name", "unknown_method")
    return_type = method_json.get("key.typename", "Void")
    parameters = []

    for param in method_json.get("key.substructure", []):
        if param.get("key.kind") == "source.lang.swift.decl.var.parameter":
            param_name = param.get("key.name", "param")
            param_type = param.get("key.typename", "Any")
            parameters.append(f"{param_name}: {param_type}")

    params_str = ", ".join(parameters)
    method_signature = f"func {method_name.split('(')[0]}({params_str}) -> {return_type}"
    
    return method_signature

def extract_instance_variables_and_methods(structure):
    instance_variables = []
    methods = []

    for item in structure.get('key.substructure', []):
        if item['key.kind'] == 'source.lang.swift.decl.var.instance':
            instance_variables.append(parse_instance_variable(item))
        elif item['key.kind'] == 'source.lang.swift.decl.function.method.instance':
            methods.append(parse_instance_method(item))

    return instance_variables, methods

def analyze_file(file_path):
    structure = run_sourcekitten(file_path)
    provides = extract_provides(structure)
    file_analysis = []

    for item in provides:
        for substructure in structure.get('key.substructure', []):
            if substructure['key.name'] == item:
                instance_variables, methods = extract_instance_variables_and_methods(substructure)
                depends = extract_depends_per_item(substructure)
                file_analysis.append({
                    "type": item,
                    "instance_variables": instance_variables,
                    "methods": methods,
                    "depends": depends
                })
    return {
        "file": file_path,
        "provides": provides,
        "details": file_analysis,
        "structure": structure
    }

def get_method_implementation(file_path, structure, method_name):
    with open(file_path, 'r') as file:
        content = file.read()

    def find_method(substructure, method_name):
        for item in substructure:
            if item.get('key.kind') == 'source.lang.swift.decl.function.method.instance' and item.get('key.name') == method_name:
                return item
            if 'key.substructure' in item:
                result = find_method(item['key.substructure'], method_name)
                if result:
                    return result
        return None

    method_structure = find_method(structure.get('key.substructure', []), method_name)
    if method_structure:
        start_offset = method_structure['key.offset']
        end_offset = method_structure['key.offset'] + method_structure['key.length']
        return content[start_offset-1:end_offset]
    else:
        return None

# Example usage:
# file_path = "path_to_your_swift_file.swift"
# structure = run_sourcekitten(file_path)
# method_name = "navigateToMissionDetails(for:)"
# method_implementation = get_method_implementation(file_path, structure, method_name)
# print(method_implementation)