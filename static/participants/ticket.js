// "Save to my phone": share the ticket image (iPhone: "Save Image" puts it in Photos), otherwise download it.
document.addEventListener('click', async function (event) {
  var button = event.target.closest && event.target.closest('[data-save-ticket]');
  if (!button) return;
  event.preventDefault();
  var url = button.getAttribute('data-file');
  var name = button.getAttribute('data-filename') || 'btw-ticket.png';
  try {
    var blob = await (await fetch(url, {credentials: 'same-origin'})).blob();
    var file = new File([blob], name, {type: 'image/png'});
    if (navigator.canShare && navigator.canShare({files: [file]})) {
      await navigator.share({files: [file], title: 'My Built to Work ticket'});
      return;
    }
    var link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    setTimeout(function () { URL.revokeObjectURL(link.href); link.remove(); }, 1000);
  } catch (error) {
    if (error && error.name === 'AbortError') return; // they closed the share sheet
    window.location.href = url;
  }
});
