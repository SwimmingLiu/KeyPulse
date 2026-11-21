---
type: regex
pattern: '我会[^"\n]{0,8}[：:][^"\n]{0,140}(keypulse|KeyPulse)'
flags: i
target: trace
---
The agent's own answer (the `- 我会：` line) must name KeyPulse as the thing
that does the measuring, rather than answering from general knowledge.

Anchored to that line so a tool call that happens to contain "keypulse scan"
cannot pass it, and scored across the trace so an agent that answers correctly
and then keeps working still gets credit for the answer.
