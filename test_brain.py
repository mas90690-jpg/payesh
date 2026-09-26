import asyncio
from openai import AsyncOpenAI

async def main():
    client = AsyncOpenAI(
        api_key="ollama",
        base_url="http://127.0.0.1:11434/v1",
        timeout=30,
    )
    try:
        r = await client.chat.completions.create(
            model="qwen2.5:3b",
            messages=[{"role": "user", "content": "سلام"}],
            temperature=0.1,
            max_tokens=200,
        )
        print("✅ OK:", r.choices[0].message.content[:100])
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")

asyncio.run(main())