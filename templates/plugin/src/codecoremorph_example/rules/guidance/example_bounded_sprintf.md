Replace each `sprintf(buffer, format, ...)` with `std::snprintf(buffer, size, format, ...)`.

- `size` is the size of the destination: `sizeof buffer` for an array, otherwise the size the
  code already knows (a parameter, a member). Never guess a size.
- Keep the format string and the arguments as they are.
- Include `<cstdio>` where the file uses `std::snprintf` and doesn't include it yet.
- When the code uses `sprintf`'s return value, keep using it: `snprintf` returns the length the
  whole output would have had, which is the same when nothing is cut off.
- If a site's destination size can't be known from the code, leave the site and say why.
