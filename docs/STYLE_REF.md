# Creator style reference

What hiclip's default edit is matched to. The figures come from 5 Shorts studied frame by frame on 24 Nov 2026:
- Snax Gaming ×2
- SoulAman
- RyzenOP
- a GTA 6 guide channel

Cuts were measured with `scdet`, loudness with `ebur128`.

| | Snax ×2 | SoulAman | RyzenOP | Guide channel |
|---|---|---|---|---|
| Length | 22 s, 30 s | 16 s | 23 s | 70 s |
| Hard cuts | 3–4, all in the ending | 0 | 0 | 20 (one every ~3.4 s) |
| Loudness (LUFS) | −18.2, −16.1 | −16.9 | −14.3 | −16.0 |
| LRA (LU) | 7.1, 14.3 | 2.5 | 4.8 | 1.6 |
| Frame rate | 60 fps | 60 fps | 30 fps | 30 fps |

## Layout
- **Snax and SoulAman:** the game is zoomed into a band, about 1080×970, sitting at y≈400.
  - The facecam is in the band's top-left corner, about 300 px wide.
  - The area above is black or a dark blurred copy of the game.
  - The heading sits just above the band.
  - hiclip layout: `band`.
- **RyzenOP:** the game on top, a white title box in the middle, the facecam filling the bottom half. Closest hiclip layout: `stack`.
- **Guide channel:** a zoomed band with a blurred fill, hiclip's `zoom`.
- **Channel tag:** every reference shows a channel tag top-left (▶ /SnaxGaming). hiclip: the clip's `watermark`.

## Text
- **Heading:** stays on screen for the whole Short.
  - Snax: red slanted condensed capitals ending in an emoji 💀. hiclip: `hook_style=red` with the emoji overlay.
  - SoulAman: white bold with the key word in yellow.
- **Captions:**
  - Snax: one word at a time, heavy sans, white or yellow (key words yellow), on the band's bottom edge. hiclip style: `punch`.
  - Guide channel: 2–3 words, white with the key word hot pink. hiclip style: `pink`.

## The ending (Snax): where the edit happens
1. The Short plays continuously until the payoff, with no jump cuts.
2. At the payoff there's a glitch: RGB split and blur, about 0.3 s.
3. Then a black-and-white reaction with the facecam blown up in the middle and a meme image (staring cat) bottom-right, about 3–4 s.
4. Then the payoff replayed in black-and-white slow motion.
5. It ends on TV colour bars and static, about 0.4 s.

Other meme cards used: "A few moments later" (SpongeBob).

hiclip's version, in `studio/edit.py`:
- payoff = the onset of the loudest second in the back half of the clip
- reaction: `REACT_MAX` 3.6 s, stretched to finish the sentence being spoken
- replay: 1.6 s before and 0.4 s after the payoff, at 0.55× speed
- `OUTRO` 0.45 s
- glitch, vine boom and TV static come from MyInstants
- the emoji is picked from the clip's category

## Pacing and sound
- Punch-in zooms (+10 %) land on loud spots, so nothing is held still for more than 7 s (`MAX_STILL`).
- A music bed from MyInstants, matched to the category's mood, is ducked under the voice with `sidechaincompress`.
- Audio is compressed lightly, since the references' LRA is 1.6–7, then levelled in two passes to −14 LUFS with true peak ≤ −1.5 dB.

## Scoring
`python tests/style_score.py clip.mp4 clip.ass edit.json` gives a 10-point score:
- heading
- captions
- nothing still for more than 8 s
- no dead air
- meme sounds
- music
- payoff emphasis
- outro
- loudness
- 1080x1920 at a constant 30/60 fps

The look itself is judged by eye, against frames from the references.
