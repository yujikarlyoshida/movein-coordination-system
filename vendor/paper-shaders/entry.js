// Housewarm's shader bundle: MeshGradient and PulsingBorder from Paper Shaders,
// without React. Defaults and prop-to-uniform mapping copied from
// @paper-design/shaders-react 0.0.43 (the version whose props the hero uses),
// over its core dependency @paper-design/shaders 0.0.39.
import {
  ShaderMount,
  ShaderFitOptions,
  defaultObjectSizing,
  getShaderColorFromString,
  getShaderNoiseTexture,
  meshGradientFragmentShader,
  pulsingBorderFragmentShader,
} from "@paper-design/shaders";

const meshDefaults = {
  ...defaultObjectSizing,
  speed: 1,
  frame: 4e4,
  colors: ["#5100ff", "#00ff80", "#ffcc00", "#ea00ff"],
  distortion: 0.8,
  swirl: 0.1,
};

const pulsingDefaults = {
  ...defaultObjectSizing,
  scale: 0.85,
  speed: 1,
  frame: 0,
  colorBack: "#000000",
  colors: ["#f2244f", "#4da6e6"],
  roundness: 0.5,
  thickness: 0.02,
  softness: 0.5,
  intensity: 2.4,
  spotsPerColor: 4,
  spotSize: 0.15,
  pulse: 0,
  smoke: 1,
  smokeSize: 1.3,
};

function sizing(p) {
  return {
    u_fit: ShaderFitOptions[p.fit],
    u_rotation: p.rotation,
    u_scale: p.scale,
    u_offsetX: p.offsetX,
    u_offsetY: p.offsetY,
    u_originX: p.originX,
    u_originY: p.originY,
    u_worldWidth: p.worldWidth,
    u_worldHeight: p.worldHeight,
  };
}

function meshGradient(el, props) {
  const p = { ...meshDefaults, ...props };
  return new ShaderMount(el, meshGradientFragmentShader, {
    u_colors: p.colors.map(getShaderColorFromString),
    u_colorsCount: p.colors.length,
    u_distortion: p.distortion,
    u_swirl: p.swirl,
    ...sizing(p),
  }, undefined, p.speed, p.frame);
}

function pulsingBorder(el, props) {
  const p = { ...pulsingDefaults, ...props };
  return new ShaderMount(el, pulsingBorderFragmentShader, {
    u_colorBack: getShaderColorFromString(p.colorBack),
    u_colors: p.colors.map(getShaderColorFromString),
    u_colorsCount: p.colors.length,
    u_roundness: p.roundness,
    u_thickness: p.thickness,
    u_softness: p.softness,
    u_intensity: p.intensity,
    u_spotsPerColor: p.spotsPerColor,
    u_spotSize: p.spotSize,
    u_pulse: p.pulse,
    u_smoke: p.smoke,
    u_smokeSize: p.smokeSize,
    u_pulseTexture: getShaderNoiseTexture(1),
    u_noiseTexture: getShaderNoiseTexture(0),
    ...sizing(p),
  }, undefined, p.speed, p.frame);
}

window.HousewarmShaders = { meshGradient, pulsingBorder };
