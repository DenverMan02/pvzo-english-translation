/// <reference path="jquery-1.4.4.js" />
/**
*base
**/
(function ($, window) {
  if (window._benSetupCore) return;
  window._benSetupCore = true;
  var _ben = {
    extend: function () {
      $.extend.apply(this, arguments);
    }
  };

  var initializing = false, fnTest = /xyz/.test(function () { xyz; }) ? /\bbase\b/ : /.*/;
  // The base Class implementation (does nothing)
  var Class = function () { };

  // Create a new Class that inherits from this class
  Class.extend = function (ns, prop) {
    if (arguments.length == 2) {
      var firstLetter = ns.split('')[0];
      if (firstLetter.toUpperCase() != firstLetter) {
        throw new Error("Class must begin with Uppercase letters:" + ns);
      }
      _ben[ns] = this.extend(prop);
      return;
    }
    var base = this.prototype, prop = arguments[0];
    initializing = true;
    var prototype = new this();
    initializing = false;
    for (var name in prop) {
      // Check if we're overwriting an existing function
      prototype[name] = typeof prop[name] == "function" &&
        typeof base[name] == "function" && fnTest.test(prop[name]) ?
        (function (name, fn) {
          return function () {
            var tmp = this.base;

            // Add a new .base() method that is the same method
            // but on the super-class
            this.base = base[name];

            // The method only need to be bound temporarily, so we
            // remove it when we're done executing
            var ret = fn.apply(this, arguments);
            this.base = tmp;

            return ret;
          };
        })(name, prop[name]) :
        prop[name];
    }

    function _Class() {
      // All construction is actually done in the init method
      if (!initializing && this.init)
        this.init.apply(this, arguments);
    }

    _Class.prototype = prototype;

    _Class.prototype.constructor = _Class;

    _Class.extend = arguments.callee;

    return _Class;
  };
  _ben.Class = Class;
  window.ue = _ben;
})(jQuery, this);