# Team photos

Drop the following three files here. The About page will pick them up
automatically:

| File | Person | Where it renders |
|------|--------|-----------------|
| `owise.jpg` | Owise Zoubi | Team card — left avatar |
| `mohamad.jpg` | Mohamad Atamneh | Team card — right avatar |
| `natalie.jpg` | Dr. Natalie Levi | Supervisor card |

## Recommended specs

- **Aspect ratio:** square (400×400 or 600×600 recommended)
- **Format:** `.jpg`, `.png`, or `.webp` all work. Keep the extension
  consistent with what's coded in `AboutPage.jsx`.
- **File size:** under 200 KB per photo (compress with
  https://tinypng.com or https://squoosh.app).
- **Framing:** center the face with ~15% margin around the head so
  the circular crop doesn't clip hair or chin.
- **Naming:** lowercase only. macOS is case-insensitive but Vercel
  is Linux (case-sensitive). `Owise.jpg` will 404 in production.

## Fallback behavior

If any file is missing, the AvatarCircle component falls back to a
gradient initial bubble automatically — no broken image icons. You
can ship with 2/3 photos and the page still looks polished.
