import os, re

# Get all scripts/*.py files
all_files = []
for f in os.listdir('scripts'):
    if f.endswith('.py'):
        all_files.append('scripts/' + f)

# Read all script files
script_contents = {}
for f in all_files:
    try:
        with open(f, 'r', encoding='utf-8') as fh:
            script_contents[f] = fh.read()
    except:
        script_contents[f] = ''

# Check which scripts import from other scripts
script_imports = {}
for f in all_files:
    content = script_contents.get(f, '')
    imported = []
    for other in all_files:
        if f == other:
            continue
        other_name = os.path.basename(other)
        other_no_ext = os.path.splitext(other_name)[0]
        if other_name in content:
            imported.append(other)
    script_imports[f] = imported

# Scripts that are NOT imported by any other script = potential entry points or dead code
print('Scripts NOT imported by any other script:')
for f in sorted(all_files):
    if f not in script_imports or not script_imports[f]:
        # Check if it's imported by src/ scripts
        found = False
        for root, dirs, files in os.walk('src'):
            for sf in files:
                if sf.endswith('.py'):
                    sf_path = os.path.join(root, sf).replace('\\', '/')
                    try:
                        with open(sf_path, 'r', encoding='utf-8') as fh:
                            content = fh.read()
                        if os.path.basename(f)[:-3] in content:
                            found = True
                            break
                    except:
                        pass
        if not found:
            print(f'  {f}')