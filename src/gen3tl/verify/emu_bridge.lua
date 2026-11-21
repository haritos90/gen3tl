-- gen3tl bridge script for mGBA 0.10+.
-- Listens on 127.0.0.1:8765.
-- One answer line per command.
-- commands: ping, frames, press, shot, save, load, r8/16/32, w8/16/32, reset.

local PORT = 8765
local server = nil
local client = nil
local queue = {}
local waiting = 0
local held = 0
local releaseAt = 0
local frame = 0
local pendingReply = nil
local buffer = ""

local KEYS = { A = 0, B = 1, SELECT = 2, START = 3, RIGHT = 4, LEFT = 5, UP = 6, DOWN = 7, R = 8, L = 9 }

local function reply(text)
  if client then client:send(text .. "\n") end
end

local function keymask(spec)
  local mask = 0
  for name in string.gmatch(spec, "[^,]+") do
    local bit = KEYS[string.upper(name)]
    if bit then mask = mask | (1 << bit) end
  end
  return mask
end

local function execute(line)
  local cmd, a, b = string.match(line, "^(%S+)%s*(%S*)%s*(%S*)")
  if cmd == "ping" then
    reply("ok")
  elseif cmd == "frames" then
    waiting = tonumber(a) or 1
    pendingReply = "ok"
  elseif cmd == "press" then
    held = keymask(a)
    emu:setKeys(held)
    waiting = tonumber(b) or 1
    releaseAt = waiting
    pendingReply = "ok"
  elseif cmd == "shot" then
    emu:screenshot(a)
    reply("ok")
  elseif cmd == "save" then
    reply(emu:saveStateFile(a, 31) and "ok" or "error")
  elseif cmd == "load" then
    reply(emu:loadStateFile(a, 31) and "ok" or "error")
  elseif cmd == "r8" then
    reply(tostring(emu:read8(tonumber(a, 16))))
  elseif cmd == "r16" then
    reply(tostring(emu:read16(tonumber(a, 16))))
  elseif cmd == "r32" then
    reply(tostring(emu:read32(tonumber(a, 16))))
  elseif cmd == "w8" then
    emu:write8(tonumber(a, 16), tonumber(b)); reply("ok")
  elseif cmd == "w16" then
    emu:write16(tonumber(a, 16), tonumber(b)); reply("ok")
  elseif cmd == "w32" then
    emu:write32(tonumber(a, 16), tonumber(b)); reply("ok")
  elseif cmd == "reset" then
    emu:reset(); reply("ok")
  else
    reply("error unknown command " .. tostring(cmd))
  end
end

local function poll()
  if not server then return end
  if not client then
    local c = server:accept()
    if c then client = c end
    return
  end
  local data, err = client:receive(1024)
  if data then
    buffer = buffer .. data
    while true do
      local nl = string.find(buffer, "\n")
      if not nl then break end
      table.insert(queue, string.sub(buffer, 1, nl - 1))
      buffer = string.sub(buffer, nl + 1)
    end
  elseif err ~= socket.ERRORS.AGAIN then
    client = nil
  end
end

local function onFrame()
  frame = frame + 1
  poll()
  if waiting > 0 then
    waiting = waiting - 1
    if waiting == 0 then
      if held ~= 0 then held = 0; emu:setKeys(0) end
      if pendingReply then reply(pendingReply); pendingReply = nil end
    end
    return
  end
  while #queue > 0 and waiting == 0 do
    execute(table.remove(queue, 1))
  end
end

server = socket.bind("127.0.0.1", PORT)
if server then
  server:listen()
  console:log("port         " .. PORT)
  console:log("bridge       ready")
else
  console:error("port         " .. PORT)
  console:error("error        bind failed")
end
callbacks:add("frame", onFrame)
