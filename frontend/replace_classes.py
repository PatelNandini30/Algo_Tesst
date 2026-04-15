import os
import re

TARGET_DIR = "/home/user/Algo_Test_Software/frontend/src/components"

MAPPINGS = [
    # Text colors
    (r'\btext-gray-900\b', 'text-primary'),
    (r'\btext-gray-800\b', 'text-primary'),
    (r'\btext-gray-700\b', 'text-secondary'),
    (r'\btext-gray-600\b', 'text-secondary'),
    (r'\btext-gray-500\b', 'text-muted'),
    (r'\btext-gray-400\b', 'text-muted'),
    (r'\btext-gray-300\b', 'text-muted'),
    
    # Background colors
    (r'\bbg-white\b', 'bg-surface'),
    (r'\bbg-gray-50\b', 'bg-hover'),
    (r'\bbg-gray-100\b', 'bg-base'),
    (r'\bbg-gray-200\b', 'bg-base'),
    (r'\bbg-gray-800\b', 'bg-surface'),
    (r'\bbg-gray-900\b', 'bg-base'),
    
    # Border colors
    (r'\bborder-gray-100\b', 'border-subtle'),
    (r'\bborder-gray-200\b', 'border-default'),
    (r'\bborder-gray-300\b', 'border-strong'),
    (r'\bborder-gray-700\b', 'border-strong'),
    (r'\bborder-gray-800\b', 'border-default'),
    
    # Financial colors
    (r'\btext-green-500\b', 'text-profit'),
    (r'\btext-green-600\b', 'text-profit'),
    (r'\btext-red-500\b', 'text-loss'),
    (r'\btext-red-600\b', 'text-loss'),
    (r'\bbg-green-50\b', 'bg-profit-bg'),
    (r'\bbg-red-50\b', 'bg-loss-bg'),
    (r'\bbg-green-100\b', 'bg-profit-bg'),
    (r'\bbg-red-100\b', 'bg-loss-bg'),
    
    # Accent colors
    (r'\btext-blue-500\b', 'text-accent'),
    (r'\btext-blue-600\b', 'text-accent'),
    (r'\bbg-blue-500\b', 'bg-accent text-inverse'),
    (r'\bbg-blue-600\b', 'bg-accent text-inverse'),
    (r'\bbg-blue-50\b', 'bg-hover'),
    (r'\bbg-blue-100\b', 'bg-hover'),
    (r'\bborder-blue-100\b', 'border-subtle'),
    (r'\bborder-blue-600\b', 'border-accent-border'),
]

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content
    for pattern, replacement in MAPPINGS:
        content = re.sub(pattern, replacement, content)

    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Updated: {os.path.basename(filepath)}")

def main():
    for root, _, files in os.walk(TARGET_DIR):
        for file in files:
            if file.endswith('.jsx') or file.endswith('.js'):
                filepath = os.path.join(root, file)
                process_file(filepath)
    print("Done replacing classes.")

if __name__ == '__main__':
    main()
