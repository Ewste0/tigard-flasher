"use strict";
// Tigard header numbering: https://github.com/tigard-tools/tigard#pinouts
// The 14-pin target layout follows the user's MPC5748G connector reference.
function wiringGuide(key, resetPin = "srst") {
  if (!["srst", "trst"].includes(resetPin)) throw new Error("Unknown reset lead");
  const mpc = key === "spc5748g" || ["spc58nh92", "spc58eh92"].includes(key), spi = key === "w25q128jv";
  const mini = ["spc560b54", "spc560b60", "spc560b64"].includes(key);
  const reset = resetPin === "trst" ? [7, "TRST", "BD4"] : [8, "SRST", "BD5"];
  const jcomp = resetPin === "trst" ? [8, "SRST", "BD5"] : [7, "TRST", "BD4"];
  const wire = (id, adapter, pins, signal, note) => ({id, adapterPin:adapter[0], adapter:adapter[1], gpio:adapter[2], pins, signal, note});
  const rows = spi ? [
    wire("clock", [6,"SCK","BD0"], [6], "CLK", "SPI clock, mode 0."),
    wire("tdi", [8,"COPI","BD1"], [5], "DI / IO0", "Data from Tigard to flash."),
    wire("tdo", [3,"CIPO","BD2"], [2], "DO / IO1", "Data from flash to Tigard."),
    wire("select", [1,"CS","BD3"], [1], "CS#", "Active-low chip select, CS0."),
    wire("wp", [5,"WP","pull-up"], [3], "WP#", "Keep write protect inactive using the header pull-up."),
    wire("hold", [4,"EN","pull-up"], [7], "HOLD# / RESET#", "Keep hold/reset inactive using the header pull-up."),
    wire("ground", [7,"GND","—"], [4], "GND", "Common ground."),
    wire("power", [2,"VTGT","—"], [8], "VCC", "Target flash I/O supply reference."),
  ] : [
    wire("tdi", [4,"TDI","BD1"], mpc ? [1] : mini ? [8] : [], "TDI", "Data from Tigard to the target."),
    wire("tdo", [5,"TDO","BD2"], mpc ? [3] : mini ? [6] : [], "TDO", "Data from the target to Tigard."),
    wire("clock", [3,"TCK","BD0"], mpc ? [5] : mini ? [4] : [], "TCK", "JTAG clock."),
    wire("mode", [6,"TMS","BD3"], mpc ? [10] : mini ? [2] : [], "TMS", "JTAG TAP state control."),
    wire("reset", reset, mpc ? [9] : mini ? [10] : [], "RESET", "Active-low MCU reset; pulsed during memory attachment."),
    ...(mpc ? [wire("jcomp", jcomp, [14], "JCOMP", "JTAG enable; held HIGH throughout attachment. Separate from MCU reset.")] : []),
    wire("ground", [2,"GND","—"], mpc ? [2,4,6,12] : mini ? [3,5] : [], "GND", "Common ground; numbered contacts follow your connector reference."),
    wire("power", [1,"VTGT","—"], mpc ? [11] : mini ? [1] : [], "VCC / VREF", "Target debug I/O supply reference; not an instruction to apply 5 V."),
  ];
  const pins = spi ? [[1,"CS#"],[8,"VCC"],[2,"DO"],[7,"HOLD#"],[3,"WP#"],[6,"CLK"],[4,"GND"],[5,"DI"]]
    : mpc ? [[1,"TDI"],[2,"GND"],[3,"TDO"],[4,"GND"],[5,"TCK"],[6,"GND"],[7,"NC"],[8,"NC"],[9,"RESET"],[10,"TMS"],[11,"VCC"],[12,"GND"],[13,"RDY / NC"],[14,"JCOMP"]]
    : mini ? [[1,"TVCC"],[2,"TMS"],[3,"GND"],[4,"TCK"],[5,"GND"],[6,"TDO"],[7,"NC"],[8,"TDI"],[9,"JTAG_MOD / NC*"],[10,"RESET"]] : [];
  return {spi, mpc, mini, rows, pins, resetLead:reset[1], jcompLead:jcomp[1],
    reference: mpc ? "/assets/jtag-14pin.svg" : mini ? "/assets/spc560-10pin.svg" : null,
    title: spi ? "SPI flash · 8-pin package" : mpc ? `${key === "spc5748g" ? "MPC5748G" : key.toUpperCase()} · 14-pin JTAG reference` : `${key.toUpperCase()} · 10-pin JTAG reference`,
    orientation: spi ? "Package top view. Pin 1 is beside the dot; numbering runs counterclockwise."
      : mpc || mini ? "Same orientation as your reference: pin 1 upper left, pin 2 upper right. This is not a mirrored cable view."
      : "Locate these signals in your board schematic. No connector numbering is assumed for this target."};
}
if (typeof module !== "undefined") module.exports = {wiringGuide};
