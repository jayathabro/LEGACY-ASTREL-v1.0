=====================================================
  DISCORD SERVER MAINTAIN BOT - සම්පූර්ණ මාර්ගෝපදේශය
=====================================================

මේ bot එක ලියලා තියෙන්නේ Python වලින්, discord.py library එක පාවිච්චි කරලා.
කෙටියෙන් කිව්වොත් - මේක server එකක් manage/maintain කරන්න ඕන සියලුම
වැදගත් moderation features තියෙන bot එකක්.


-----------------------------------------------------
1. FOLDER එකේ තියෙන FILES මොනවද?
-----------------------------------------------------

bot.py            -> bot එක run කරන main file එක (මේකයි run කරන්නේ)
config.py         -> .env file එකෙන් settings load කරගන්නා file එක
database.py       -> warnings සහ server settings save කරගන්නා database code
utils.py          -> Bot ඇතුළේ පොදුවේ පාවිච්චි වෙන helper functions
cogs\moderation.py -> kick/ban/warn/purge වගේ moderation commands
cogs\utility.py    -> ping/serverinfo/userinfo වගේ commands
.env.example      -> Token දාන්න ඕන file එකේ sample එක
requirements.txt  -> Bot එකට ඕන Python packages list එක
data\bot.db        -> Bot run කරාම හැදෙන database file එක (warnings save වෙන්නේ මෙතන)


-----------------------------------------------------
2. BOT එක SETUP කරන විදිය (පියවර පියවරෙන්)
-----------------------------------------------------

පියවර 1: Discord Developer Portal එකේ Bot එකක් හදන්න
   - https://discord.com/developers/applications යන්න
   - "New Application" click කරලා bot එකකට නමක් දෙන්න
   - වම් පැත්තේ "Bot" tab එකට යන්න
   - "Reset Token" click කරලා TOKEN එක copy කරගන්න (මේක ගොඩක් important -
     කවුරුත් එක්ක share කරන්න එපා, share උනොත් bot එක hack වෙන්න පුළුවන්)
   - "Privileged Gateway Intents" කියන කොටසේ "SERVER MEMBERS INTENT" එක
     ON කරන්න (මේක නැතුව kick/ban/warn commands වැඩ කරන්නේ නෑ)

පියවර 2: .env file එක හදන්න
   - .env.example file එක copy කරලා නමින් .env කියලා rename කරන්න
   - .env file එක Notepad එකෙන් open කරලා:
       DISCORD_TOKEN=oyage_token_eka_methana_danna
     කියලා token එක paste කරන්න
   - Test කරන server එකේ ID එක දාන්න ඕන නම් DEV_GUILD_ID= කියන එකට
     server ID එක දාන්න (මේකෙන් slash commands ඉක්මනටම update වෙනවා,
     නැත්නම් global sync එකකට පැයක් විතර යනවා)

පියවර 3: Bot එක server එකට invite කරන්න
   - Developer Portal එකේ OAuth2 -> URL Generator එකට යන්න
   - Scopes එකේ "bot" සහ "applications.commands" දෙකම check කරන්න
   - Bot Permissions එකේ මේවා දාන්න:
       Kick Members, Ban Members, Moderate Members,
       Manage Messages, Manage Channels, Send Messages, Embed Links
   - හැදෙන URL එක browser එකේ paste කරලා oyage server එකට bot එක add කරගන්න

පියවර 4: Bot එක run කරන්න
   - Terminal/PowerShell එකේ මේ folder එකට යන්න
   - මේ command එක run කරන්න:
       .\venv\Scripts\python.exe bot.py
   - "Logged in as..." කියලා පෙන්නුවොත් bot එක online!


-----------------------------------------------------
3. BOT එකේ COMMANDS - මොනවද කරන්නේ කියලා
-----------------------------------------------------

Discord එකේ "/" ටයිප් කරාම මේ commands ටික auto-complete වෙනවා.


[[ MODERATION COMMANDS - Server Manage කරන්න ]]

/kick member reason
   -> කියන member කෙනාව server එකෙන් kick කරනවා. reason එකක් optional.

/ban member reason delete_message_days
   -> Member කෙනාව server එකෙන් permanent ban කරනවා. delete_message_days
      කිව්වොත් (0-7) ඒ දවස් ගාණකට ආපු message ටිකත් delete වෙනවා.

/unban user_id reason
   -> කලින් ban කරපු කෙනෙක්ගේ user ID එක දීලා ආයෙත් server එකට එන්න
      ඉඩ දෙනවා.

/timeout member minutes reason
   -> Member කෙනාව minutes ගාණකට "mute" කරනවා (server එකේ කතා කරන්න
      බෑ, channel බලන්න පුළුවන්). Max 40320 minutes (දවස් 28).

/untimeout member
   -> Timeout එකේ ඉන්න කෙනෙක්ව ඉක්මනටම free කරනවා.

/warn add member reason
   -> Member කෙනාට warning එකක් දෙනවා. Database එකේ save වෙනවා.
      Member කෙනාට DM එකකින් දන්නවනවා (DM open නම්).

/warn list member
   -> කියන member කෙනාට ලැබිලා තියෙන සියලුම warnings list කරලා පෙන්නනවා.

/warn remove warning_id
   -> Warning ID එකක් දීලා (/warn list වලින් පේන ID එක) ඒක විතරක් remove
      කරනවා.

/warn clear member
   -> Member කෙනාගේ සියලුම warnings එකවර clear කරනවා.

/purge amount member
   -> Channel එකේ ආපු ලාස්ට් message ටික (max 100) delete කරනවා.
      member කෙනෙක්ගේ නම දුන්නොත් ඒ කෙනාගේ messages විතරක් delete වෙනවා.

/lock reason
   -> Channel එකේ @everyone කෙනාට message යවන්න බෑ කියලා lock කරනවා.

/unlock
   -> Lock කරපු channel එක ආයෙත් unlock කරනවා.

/setmodlog channel
   -> කියන channel එකට moderation actions ටික (kick, ban, warn වගේ)
      auto-log වෙන්න channel එකක් set කරනවා. (Admin permission ඕන)


[[ UTILITY COMMANDS - Info බලාගන්න ]]

/ping
   -> Bot එකේ latency (speed) එක බලනවා.

/serverinfo
   -> Server එකේ member count, roles ගාණ, channels ගාණ, boost level,
      server හැදුවේ කවදද වගේ details පෙන්නනවා.

/userinfo member
   -> Member කෙනෙක්ගේ roles, server එකට join උනේ කවදද, account එක
      හැදුවේ කවදද වගේ details පෙන්නනවා.

/avatar member
   -> Member කෙනෙක්ගේ profile picture එක big size එකෙන් පෙන්නනවා.


-----------------------------------------------------
4. PERMISSION SAFETY - මොකද මේ features ටික තියෙන්නේ
-----------------------------------------------------

- Server Owner කෙනාව කවුරුවත් moderate කරන්න බෑ.
- ඔයාට වඩා role එකක් උස මිනිහෙක්ව හෝ ඔයාගේම level එකේ කෙනෙක්ව
  moderate කරන්න බෑ (Owner නම් හැර).
- Bot එකේ role එකට වඩා උස role එකක් තියෙන කෙනෙක්ව bot එකටත් touch
  කරන්න බෑ - මේක Discord permission system එකේම rule එකක්.
- Permission නැති කෙනෙක් command එකක් run කරන්න try කලොත් Discord
  එකෙන්ම ඒ command එක menu එකේ පෙන්නන්නේ නෑ.


-----------------------------------------------------
5. DATA SAVE වෙන්නේ කොහෙද?
-----------------------------------------------------

Warnings සහ mod-log channel setting වගේ දේවල් save වෙන්නේ
data\bot.db කියන file එකේ (SQLite database එකක්). Bot එක restart
කලත් මේ data ටික නැති වෙන්නේ නෑ.


-----------------------------------------------------
6. VPS/SERVER එකක 24/7 RUN කරගන්නා විදිය (Optional)
-----------------------------------------------------

Local computer එකේ run කරනවනම් computer එක off උනාම bot එකත් off
වෙනවා. 24/7 online තියාගන්න ඕන නම් Railway, Render වගේ hosting
service එකකට upload කරලා run කරන්න පුළුවන් - ඒක ගැන ඕන නම් වෙන
වෙලාවක අහන්න.

=====================================================
  ඉවරයි! ප්‍රශ්නයක් ආවොත් අහන්න.
=====================================================
