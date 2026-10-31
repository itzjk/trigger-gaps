Output docs/demo.gif
Set Shell bash
Set FontSize 17
Set Framerate 8
Set Width 1160
Set Height 880
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 32ms
Hide
Type "source .venv/bin/activate; PS1='$ '; clear"
Enter
Sleep 500ms
Show
Type "trigger-gaps check .work/demo/before.sqlite"
Enter
Sleep 8s
Ctrl+L
Sleep 300ms
Type "trigger-gaps check .work/demo/after.sqlite"
Enter
Sleep 7s
