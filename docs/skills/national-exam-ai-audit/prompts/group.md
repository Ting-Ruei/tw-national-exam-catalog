# Group lane

Inspect explicit ranges, counts, continuation markers, shared stems, and previous/current/next context.

Route likely groups to `group`. A weak cue such as `下列資料` alone is insufficient. Do not write a final
group confirmation, sequence, or shared stem. Boundary damage belongs to parser.

`承上題`、`呈上題`、`上題`、`前述` are structural continuation markers. Keep the source
wording unchanged and return only a `group_ref` dependency; the Review UI group page owns
the neighbor/range decision.
