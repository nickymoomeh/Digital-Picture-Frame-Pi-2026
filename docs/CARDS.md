# Local information cards

Cards use the existing image loader, OpenGL renderer and fade transition on
DRM/KMS. They never enter the library, playlist history, shuffle order or play
counts. The next normal advance consumes exactly the next photo as before.
No desktop, network data source or additional dependency is required.

## Configuration

Set these optional values in the frame's local YAML file:

```yaml
slideshow:
  card_directory: /home/pi4/FrameCards
  card_every: 8
  card_consecutive: false
```

An empty `card_directory` (the default) disables queueing. The directory cannot
be changed through remote settings commands. Keep it outside scanned photo
folders so generated images are not also indexed as photographs. The frame's
service user needs read access. Paths must resolve to regular files inside this
directory; symlinks escaping it are refused. Supported cards are PNG, JPEG and
WebP. Existing decode size limits still apply.

## Queue a card

Use the existing authenticated command endpoint (include your configured HTTP
credentials when applicable):

```sh
curl -H 'Content-Type: application/json' \
  -d '{"action":"queue_card","path":"/home/pi4/FrameCards/weather.png","duration":30,"type":"weather"}' \
  http://localhost:9000/api/command
```

Use your configured HTTP port. `duration` defaults to 30 seconds and must be a
finite number from 1 to 3600. `type` defaults to the filename stem; it must be a
nonempty string of at most 128 characters. Queueing a pending type replaces its
path and duration without moving its place in line. The queue holds at most 64
different pending types and lives in memory (it is lost on restart).

The HTTP endpoint validates paths and durations before accepting a request.
An accepted command is asynchronous, as with existing commands. Files are
checked again when due; unreadable, missing, invalid or oversized cards are
logged and skipped without modifying the library. Invalid requests from other
command sources are logged and ignored.

Cards appear at a natural automatic slide boundary after `card_every` normal
photo slides (minimum one), including photos shown before queueing. Paired
portraits count as one slide; videos do not count, and their playback finishes
before a card can appear. Each card is a one-shot request: queue it again for
another appearance. External generators can queue updated cards periodically;
there is no collector or recurring timer in the frame.

Different types are served in insertion order. By default another full photo
gap follows each card. Set `card_consecutive: true` to drain waiting cards at
successive card boundaries instead. Explicit navigation commands still move
slides; they can dismiss the displayed card. Back and gallery jumps retain
their normal playlist semantics.

A card receives its requested duration **after** the configured fade-in time.
Pausing or switching the display off freezes its remaining time, including
nested pause/off states. Resuming restores the remainder. Normal photo timing
keeps its existing behavior.

## Generate a local test image safely

This uses Pillow, which the frame already requires. Run it as a user who can
write to the configured directory (create that directory first):

```python
from pathlib import Path
import os
import tempfile
from PIL import Image, ImageDraw

folder = Path('/home/pi4/FrameCards')
image = Image.new('RGB', (1920, 1200), '#17324d')
ImageDraw.Draw(image).text((160, 160), 'Local information card', fill='white', font_size=72)
fd, temporary = tempfile.mkstemp(dir=folder, suffix='.tmp')
try:
    with os.fdopen(fd, 'wb') as output:
        image.save(output, format='PNG')
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, folder / 'weather.png')
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
```

Always write a complete temporary file **in the same directory/filesystem**,
close it, then atomically rename it over the final filename. Do not overwrite
the visible PNG in place: readers could see a partial file. Cards bypass photo
prefetch and decode from disk only when due, so an atomic replacement before
that load is reflected on screen. Replacing an already displayed card does not
change its current texture; queue it again for the next appearance.

## State and interface

`/api/state` includes an additive `card` object:

```json
{"active":true,"type":"weather","remaining":29.5,"pending":1,"photos_until_next":8}
```

`remaining` includes any fade-in time still outstanding and freezes while held.
`photos_until_next` is the photo-gap counter, even when nothing is queued.
During a card, `current` is empty rather than reporting the previous photograph.
Playlist state remains unchanged. Now Playing shows the card type and pending
count; the existing screenshot preview can show the actual card. No new file
serving endpoint exposes the card directory.

The controller serializes card loads with photo navigation. Cards intentionally
avoid photo captions and Ken Burns movement to keep text readable, and use a
fade regardless of the photo transition selection. Existing clock and user
overlays remain available. Hardware rendering must still be verified on a Pi;
unit tests substitute textures on systems without EGL/DRM.
