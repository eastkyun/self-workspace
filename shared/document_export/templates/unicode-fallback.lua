-- Keep task-list semantics consistent with the web viewer and route symbols
-- outside Noto CJK's dependable text coverage through explicit XeTeX fonts.
local function latex_escape(value)
  return value:gsub("([\\{}%%#$&_])", "\\%1")
end

local function symbol_font(codepoint)
  if codepoint >= 0x1F000 then
    return "workspaceemojifont"
  end
  if (codepoint >= 0x2190 and codepoint <= 0x2BFF)
      or (codepoint >= 0x2300 and codepoint <= 0x27FF) then
    return "workspacefallbackfont"
  end
  return nil
end

function Str(element)
  local value = element.text:gsub("☒", "☑")
  if FORMAT ~= "latex" then
    return pandoc.Str(value)
  end

  local result = pandoc.List()
  local plain = ""
  local function flush_plain()
    if plain ~= "" then
      result:insert(pandoc.Str(plain))
      plain = ""
    end
  end

  for _, codepoint in utf8.codes(value) do
    -- Variation selectors and zero-width joiners are meaningful to color emoji
    -- shaping but unsupported by the deterministic monochrome fallback.
    if codepoint ~= 0xFE0F and codepoint ~= 0x200D then
      local character = utf8.char(codepoint)
      local font = symbol_font(codepoint)
      if font then
        flush_plain()
        result:insert(pandoc.RawInline(
          "latex",
          "{\\" .. font .. " " .. latex_escape(character) .. "}"
        ))
      else
        plain = plain .. character
      end
    end
  end
  flush_plain()
  return result
end
