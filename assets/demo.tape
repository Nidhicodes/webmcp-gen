# VHS tape for the webmcp-gen demo GIF.
# Render with:  vhs assets/demo.tape   (run from the repo root)
# Requires LLM_API_KEY in the environment for --groq.

Output assets/demo.gif

Set Theme "Catppuccin Mocha"
Set FontSize 18
Set Width 1100
Set Height 720
Set Padding 24
Set TypingSpeed 60ms

# --- the hook ---
Type "# Point it at any website. Get back tools an AI agent can call."
Enter
Sleep 1s

Type "webmcp-gen https://news.ycombinator.com --groq"
Sleep 800ms
Enter

# let the real run happen: extract -> LLM analyze -> print tools
Sleep 12s

# hold the final JSON on screen so viewers can read it
Sleep 4s
