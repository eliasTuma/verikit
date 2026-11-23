// Karma configuration for `ng test` (Angular's @angular/build:karma builder reads it via
// angular.json "karmaConfig"). Two additions to what `ng generate config karma` writes:
//  - ChromeHeadlessCI: headless Chrome with --no-sandbox, which Linux CI often needs (Chrome
//    will not start its sandbox as root, and Ubuntu 23.10+ restricts its user namespaces).
//  - client.verikitSeed: this file runs in Node, the specs run in the browser, so this is how
//    VERIKIT_SEED reaches them. Specs read it from __karma__.config.verikitSeed.
module.exports = function (config) {
  config.set({
    basePath: '',
    frameworks: ['jasmine'],
    plugins: [require('karma-jasmine'), require('karma-chrome-launcher')],
    client: {
      jasmine: {},
      verikitSeed: process.env.VERIKIT_SEED || '',
    },
    reporters: ['progress'],
    browsers: ['Chrome'],
    customLaunchers: {
      ChromeHeadlessCI: {
        base: 'ChromeHeadless',
        flags: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
      },
    },
    restartOnFileChange: true,
  });
};
