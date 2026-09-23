with open('core/app/orchestrator.py', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines[420:480], start=421):
    print(f"{i:4} | {line.rstrip()}")