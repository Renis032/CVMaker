setInterval(async () => {
  try {
    const r = await fetch("/settings/telegram/status");
    if ((await r.json()).linked) location.reload();
  } catch (e) {}
}, 3000);

