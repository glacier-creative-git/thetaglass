// PM2 process config for the Thetaglass Timekeeper (Clock 1).
//
// PM2 is a Node.js process manager (npm i -g pm2) — it just supervises our Python
// daemon: keeps it alive, restarts on crash, and captures logs. The daemon itself is
// pure Python and holds no state in memory (everything is in var/thetaglass.db), so
// restarts are lossless.
//
//   pm2 start ecosystem.config.js     # launch
//   pm2 logs thetaglass-timekeeper    # tail logs
//   pm2 restart thetaglass-timekeeper # after a code change
//   pm2 stop thetaglass-timekeeper    # halt
//   pm2 save && pm2 startup           # persist across reboots
//
// Eventually this same daemon is the single process inside the Docker image.
module.exports = {
  apps: [
    {
      name: "thetaglass-timekeeper",
      // The venv console-script is a real executable (shebang -> venv python), so we
      // run it directly rather than through a node/python interpreter.
      script: "./.venv/bin/tg",
      args: "run",
      interpreter: "none",
      cwd: "/home/elester/thetaglass",
      env: {
        THETAGLASS_HOME: "/home/elester/thetaglass",
      },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000, // back off 5s between restarts on a crash loop
      out_file: "var/logs/timekeeper.out.log",
      error_file: "var/logs/timekeeper.err.log",
      time: true, // prefix PM2 log lines with timestamps
    },
  ],
};
