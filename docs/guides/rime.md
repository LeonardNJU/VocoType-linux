# Rime integration

VoCoType's IBus engine links directly to the system `librime` C API through
`rime_get_api()`. There is no Python binding or ctypes layer.

- Shared Rime data: distribution-provided, usually `/usr/share/rime-data`
- VoCoType user data: `~/.config/vocotype/rime`
- Selected schema: `previously_selected_schema` in `~/.config/vocotype/rime/user.yaml`

The schema can be changed in **VoCoType Settings → General → IBus: Rime
input scheme**. The row is shown only when IBus is selected as the lifecycle
framework. Deploy or repair data with:

```bash
vocotype-ibus-engine --deploy-rime
```

Ordinary keyboard input, preedit, candidate lists, selection, paging, and commit
are handled inside the compiled IBus engine. Voice hotkeys use the same native
core and recorder as Fcitx 5.
