# The README demo, as a script. Render it with vhs (https://github.com/charmbracelet/vhs):
#
#     vhs docs/assets/demo.tape
#
# table-talk must be on PATH (./install.sh). Every command below is a real
# invocation — ids are captured from stdout the same way the README's Use block
# does it, because the 4-hex id is minted at record time and cannot be hardcoded.

Output docs/assets/demo.gif

Set Shell bash
Set FontSize 16
Set Width 1200
Set Height 700
Set Padding 20
Set TypingSpeed 30ms
Set Theme "Gruvbox Dark"

# Record into a throwaway log rather than the viewer's real one.
Hide
Type "export TABLE_TALK_DIR=$(mktemp -d)" Enter
Type "clear" Enter
Show

Type "id=$(table-talk action 'Choose the reference genome' --why 'blocks training' --rec 'R64-1-1'); echo $id" Enter
Sleep 1s

Type "tid=$(table-talk task 'Pre-train GPN on 1,011 yeast genomes'); echo $tid" Enter
Sleep 1s

Type "table-talk progress $tid 'epoch 3/10'" Enter
Sleep 1s

Type "table-talk term 'PPL' --intuitive 'how surprised the model is by a sequence' --technical 'exp of the mean per-token negative log-likelihood'" Enter
Sleep 1s

Type "table-talk done $id" Enter
Sleep 1s

Type "table-talk show" Enter
Sleep 4s
