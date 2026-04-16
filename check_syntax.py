import ast, sys
sys.stdout.reconfigure(encoding='utf-8')
try:
    with open('c:/Users/rocks/Desktop/test/app_onboarding/app.py', encoding='utf-8') as f:
        src = f.read()
    ast.parse(src)
    print('SYNTAX OK')
except SyntaxError as e:
    print(f'SYNTAX ERROR line {e.lineno}: {e.msg}')
    print(f'  -> {e.text}')
