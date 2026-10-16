// Read the thread open in Outlook on the web's reading pane into the app's
// message shape. Evaluated in the page by outlook_web.py; returns a JSON string.
//
// Read-only: no clicks, no navigation. Layout learned from the live page:
//
//   [heading: thread subject]
//   per message:  [avatar initials] [sender line] [Reply/Reply all/Forward]
//                 [To: recipients] [date "Tue 9/22/2026 1:23 PM"]
//                 [body: aria-label="Message body" or role="document"]
//
// The sender line is an address when the sender is outside the directory (the
// lease emails) and a display name otherwise (colleagues' replies), so both are
// returned and triggers.is_trigger_sender decides which to use.
(() => {
  const main = document.querySelector('[role="main"]') || document.body;
  const BODY = '[aria-label="Message body"], [role="document"]';
  const bodies = [...main.querySelectorAll(BODY)];
  const text = e => (e.innerText || '').replace(/[   ]/g, ' ').replace(/\s+/g, ' ').trim();
  // Outlook draws icons with a private-use font; they are not text.
  const visible = t => t.replace(/[-]/g, '').replace(/\s+/g, ' ').trim();

  const heading = main.querySelector('[role="heading"]');
  const subject = heading ? visible(text(heading)) : '';
  const selected = document.querySelector('[role="option"][aria-selected="true"][data-convid]');
  const convId = selected ? selected.getAttribute('data-convid') : '';
  const ADDRESS = /^[^\s@<>]+@[^\s@<>]+\.[a-z]{2,}$/i;
  const NAME = /^[A-ZÀ-ɏ][\w'À-ɏ.-]*(?: [A-ZÀ-ɏ][\w'À-ɏ.-]*){1,3}$/;
  const DATE = /\b\d{1,2}\/\d{1,2}\/\d{4},? \d{1,2}:\d{2}\s?[AP]M\b|\b[A-Z][a-z]+day,? [A-Z][a-z]+ \d{1,2},? \d{4},? \d{1,2}:\d{2}\s?[AP]M\b/;
  const all = [...main.querySelectorAll('*')];

  return JSON.stringify(bodies.map((b, i) => {
    const prev = bodies[i - 1];
    // Everything between the previous body and this one, outside the subject.
    const header = all.filter(e =>
      !b.contains(e) && !e.contains(b) &&
      (!prev || (!prev.contains(e) && !e.contains(prev))) &&
      (!heading || !heading.contains(e)) &&
      (b.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_PRECEDING) &&
      (!prev || (prev.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING)));

    // The sender line comes before the "To:" label.
    const toIndex = header.findIndex(e => /^to:/i.test(visible(text(e))));
    const before = toIndex >= 0 ? header.slice(0, toIndex) : header;
    let sender = '', senderName = '';
    for (const e of before) {
      const t = visible(text(e));
      // Avatar initials ("AB") come first and are skipped; the sender is the
      // first thing after them that reads as an address or a name.
      if (t.replace(/^[A-Z]{1,3} /, '') !== t && (ADDRESS.test(t.slice(t.indexOf(' ') + 1)) || NAME.test(t.slice(t.indexOf(' ') + 1)))) continue;
      if (ADDRESS.test(t)) { sender = t; break; }
      if (NAME.test(t)) { senderName = t; break; }
    }

    const dated = header.map(e => text(e)).find(t => DATE.test(t)) || '';
    const received = (dated.match(DATE) || [''])[0];

    return {
      conv_id: convId,
      index: i,
      subject,
      sender,
      sender_name: senderName,
      received_text: received,
      body: (b.innerText || '').replace(/[   ]/g, ' '),
    };
  }));
})()
