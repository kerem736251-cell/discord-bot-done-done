# Quantix Discord Vouch -> Website API

The bot sends an HTTP `POST` to `REVIEW_API_URL` whenever a vouch is posted.

Authentication header (when `REVIEW_API_SECRET` is set):

`Authorization: Bearer <REVIEW_API_SECRET>`

Example JSON payload:

```json
{
  "source": "discord",
  "approved": true,
  "status": "approved",
  "review": "Amazing tweaks, everything feels smoother.",
  "rating": 5,
  "discord_user_id": "123456789",
  "discord_username": "customername",
  "discord_display_name": "Customer Name",
  "discord_avatar_url": "https://cdn.discordapp.com/...",
  "discord_guild_id": "1539742147371081738",
  "discord_channel_id": "...",
  "discord_message_id": "..."
}
```

`rating` is either `1`-`5` or `null`.

The website endpoint should verify the Bearer secret, create/update the review using `discord_message_id` as a unique key, and store it with `approved=true` / `status="approved"`. That makes Discord reviews appear as accepted in the existing admin panel without manual approval.

Recommended route:

`POST /api/reviews/discord`

Return any 2xx status when saved successfully.
