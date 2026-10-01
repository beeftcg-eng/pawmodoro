// send-reminders - Supabase Edge Function that sends Pawmodoro's phone
// notifications as Web Push, so they arrive even when the phone app is
// closed: checklist reminders, shared-list items, and the phone timer.
//
// A pg_cron job (supabase/push_reminders.sql) calls it every 30 seconds with
// the secret from the server_settings table; anyone else is turned away.
// due_push_reminders() (schema.sql) hands out what's due and claims it;
// whatever reached at least one of a person's phones is reported back with
// mark_push_sent(), and anything that failed comes round again a couple of
// minutes later.
//
// Secrets (supabase secrets set ...): VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY
// (`npx web-push generate-vapid-keys`; the public half is also in
// docs/app.js). SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY
// are provided by Supabase itself.
import webpush from "npm:web-push@3.6.7";
import { createClient } from "npm:@supabase/supabase-js@2";

const SUBJECT = "https://github.com/beeftcg-eng/pawmodoro";

type Due = {
  endpoint: string; p256dh: string; auth: string;
  kind: "daily" | "every" | "snooze" | "shared" | "timer";
  ref: string; owner: string; occurrence: string | null;
  title: string; body: string; token: string | null;
};

Deno.serve(async (req) => {
  const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

  const { data: allowed } = await db.rpc("check_cron_secret", { p_secret: req.headers.get("x-cron-secret") ?? "" });
  if (allowed !== true) return Response.json({ error: "forbidden" }, { status: 403 });

  const publicKey = Deno.env.get("VAPID_PUBLIC_KEY");
  const privateKey = Deno.env.get("VAPID_PRIVATE_KEY");
  if (!publicKey || !privateKey) {
    return Response.json({ error: "VAPID keys aren't set" }, { status: 500 });
  }
  webpush.setVapidDetails(SUBJECT, publicKey, privateKey);

  const { data, error } = await db.rpc("due_push_reminders");
  if (error) return Response.json({ error: error.message }, { status: 500 });
  const due = (data ?? []) as Due[];

  // The notification's Snooze / Done buttons call push_action() straight
  // from the phone's service worker, with this push's one-time token.
  const api = { url: Deno.env.get("SUPABASE_URL"), key: Deno.env.get("SUPABASE_ANON_KEY") };

  const delivered = new Map<string, Due>();  // one per item, however many phones
  let dropped = 0, failed = 0;
  await Promise.all(due.map(async (row) => {
    const itemKey = `${row.kind}|${row.ref}|${row.owner}|${row.occurrence}`;
    const actions = row.kind === "timer" ? []
      : row.kind === "shared" ? [{ action: "done", title: "✅ Done" }]
      : [{ action: "done", title: "✅ Done" }, { action: "snooze", title: "💤 15 min" }];
    const payload = JSON.stringify({
      title: row.title, body: row.body,
      tag: row.kind === "timer" ? "pawmodoro-timer" : `${row.kind === "shared" ? "shared" : "task"}-${row.ref}`,
      actions, token: row.token, api,
    });
    try {
      await webpush.sendNotification(
        { endpoint: row.endpoint, keys: { p256dh: row.p256dh, auth: row.auth } },
        payload, { TTL: 60 * 60, urgency: "high" },
      );
      delivered.set(itemKey, row);
    } catch (err) {
      const status = (err as { statusCode?: number }).statusCode;
      if (status === 404 || status === 410) {
        // That phone unsubscribed or the app was removed: forget it.
        await db.rpc("drop_push_subscription", { p_endpoint: row.endpoint });
        dropped++;
      } else {
        console.error("push failed", status, err);
        failed++;
      }
    }
  }));

  if (delivered.size) {
    const items = [...delivered.values()].map(({ kind, ref, owner, occurrence }) => ({ kind, ref, owner, occurrence }));
    const { error: markError } = await db.rpc("mark_push_sent", { p_items: items });
    if (markError) console.error("mark_push_sent failed", markError.message);
  }
  return Response.json({ sent: delivered.size, dropped, failed });
});
