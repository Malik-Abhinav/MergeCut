# MergeCut

MergeCut detects semantic conflicts between independently edited versions of the same source video.

Two edits may be safe individually and merge cleanly at the timeline level, yet together remove or alter important meaning. MergeCut analyzes the rendered audiovisual content to detect those cross-edit semantic conflicts.

Built for MiniMax Week using MiniMax models served through GMI Cloud.

## Status

Early development.

## Core idea

BASE + Branch A + Branch B

→ detect mechanical changes  
→ reason about semantic interactions  
→ flag meaning-level conflicts  
→ resolve supported conflicts  
→ render merged video  
→ verify final output