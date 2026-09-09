# Provider marks

Claude and OpenAI PNG marks are from LobeHub's lobe-icons collection:
https://github.com/lobehub/lobe-icons/tree/master/packages/static-png/dark

Downloaded 2026-09-10, used to identify the respective providers in a personal
usage widget. Brand marks remain the property of their respective owners;
this widget is not an official Anthropic, OpenAI, or Samsung product.

R7 uses the matching original SVG assets from
https://github.com/lobehub/lobe-icons/tree/master/packages/static-svg/icons
(`claude.svg`, `openai.svg`). `prepare_vector_marks.py` applies a 24→100 viewport
transform with fontTools and retains the source curves in `provider_paths.json`.
Kustom renders these licensed contours directly as Shape Paths; no traced or
handcrafted substitute and no external image lookup is used for the R7 logos.
Run the preparation script with fonttools installed to regenerate the path data.

## Korean typography

Noto Sans KR variable source:
https://github.com/google/fonts/tree/main/ofl/notosanskr

Licensed under SIL OFL 1.1; see OFL.txt. The packaged Regular and Medium TTFs
are static weight-400 and weight-500 instances, made with fontTools varLib
instancer for Android compatibility. The full Korean character set is retained.
No Samsung or Apple fonts are redistributed. Rebuild from NotoSansKR.ttf with:

```
fonttools varLib.instancer NotoSansKR.ttf wght=400 --output NotoSansKR-Regular.ttf
fonttools varLib.instancer NotoSansKR.ttf wght=500 --output NotoSansKR-Medium.ttf
```

## MIT License

Copyright (c) 2023 LobeHub

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
