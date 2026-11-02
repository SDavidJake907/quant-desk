import os, re

# Get all Python source files (excluding tests, research_gpu, tmp, .venv)
all_files = []
for root, dirs, files in os.walk('.'):
    skip_dirs = ['.venv', '.git', '__pycache__', 'tests', 'research_gpu', 'tmp']
    if any(x in root for x in skip_dirs):
        continue
    for f in files:
        if f.endswith('.py'):
            all_files.append(os.path.join(root, f).replace('\\', '/').replace('./', ''))

# Collect all imports
imported_modules = set()
for f in all_files:
    try:
        with open(f, 'r', encoding='utf-8') as fh:
            content = fh.read()
        for m in re.finditer(r'from\s+([\w.]+)\s+import|^import\s+([\w.]+)', content, re.MULTILINE):
            mod = m.group(1) or m.group(2)
            parts = mod.split('.')
            imports.add(parts[0])
    except:
        pass

print("Modules imported in non-test code:")
for m in sorted(imports):
    print(f"  {m}")
print(f"\nTotal unique modules imported: {len(imports)}")

print("\n--- Files that might be unused (not imported by anything) ---")
# Check which source files are never imported
for f in sorted(all_files):
    f_name = os.path.basename(f)
    f_name_no_ext = os.path.splitext(f_name)[0]
    found = False
    for other in all_files:
        if f == other:
            continue
        try:
            with open(other, 'r', encoding='utf-8') as fh:
                content = fh.read()
            if f_name_no_ext in content:
                found = True
                break
        except:
            pass
    if not found:
        print(f"  {f}")