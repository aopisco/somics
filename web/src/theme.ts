/** The SDS light theme, with the accent moved from indigo onto blue.
 *
 * SDS v24 derives every semantic accent — button fills, checkbox ticks, focus
 * rings, links — from `colors.indigo`, which renders purple. The design packet
 * specifies the blue ramp (`#1a6cef` at 500), which is SDS's own `colors.blue`.
 * Swapping the ramp keeps every component consistent instead of restyling each
 * one at the call site.
 */

import { createTheme } from "@mui/material/styles";
import { defaultAppTheme, makeSdsSemanticAppTheme, makeThemeOptions } from "@czi-sds/components";

const colors = { ...defaultAppTheme.colors, indigo: defaultAppTheme.colors.blue };

export const somicsTheme = createTheme(makeThemeOptions(makeSdsSemanticAppTheme(colors), "light"));
