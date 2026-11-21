#!/usr/bin/env python3
"""Check that your Claude API key works, using one tiny request (a few tokens).

Setup, once per terminal window, from the project folder:
    source .venv/bin/activate
    export ANTHROPIC_API_KEY="your-key-here"

Run:
    python3 -I scripts/check_api.py

The key is read from the ANTHROPIC_API_KEY environment variable. It is never written to a file.
Not tested against the live API by its author; if it fails, the message below says why.
"""
import argparse
import os
import sys

try:
    import anthropic
except ImportError:
    sys.exit("The 'anthropic' package is not installed. Activate your virtual environment "
             "(source .venv/bin/activate) and run: pip install anthropic")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="claude-haiku-5-5", help="Model to test (a small model keeps tests cheap)")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set in this terminal window.\n"
              'Run: export ANTHROPIC_API_KEY="your-key-here"   (then run this script again)', file=sys.stderr)
        return 1

    client = anthropic.Anthropic()  # reads the key from the environment
    try:
        reply = client.messages.create(
            model=args.model,
            max_tokens=20,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
    except anthropic.AuthenticationError:
        print("The API rejected the key. Check that you copied all of it and that it has not been deleted.", file=sys.stderr)
        return 1
    except anthropic.NotFoundError:
        print(f"The model name '{args.model}' was not found for your account. Try --model with a model listed in the Console docs.", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as e:
        print(f"The API returned an error (status {e.status_code}): {e.message}\n"
              "If it mentions credits or billing, check the Billing page in the Console.", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError:
        print("Could not reach the API. Check your internet connection.", file=sys.stderr)
        return 1

    print(f"Key works. Model said: {reply.content[0].text.strip()!r}")
    print(f"Tokens used: {reply.usage.input_tokens} in, {reply.usage.output_tokens} out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
