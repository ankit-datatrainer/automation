module.exports = {
  apps: [
    {
      name: "vfs-automation",
      script: "./start_vps.sh",
      interpreter: "bash",
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: "1G",
      env: {
        PORT: 4140,
        HOST: "0.0.0.0",
        PYTHONUNBUFFERED: "1"
      }
    }
  ]
};
