$(document).ready(function(){
	 //mouse-hover
	$('.js-hover0 li').hover(
		function(){$(this).addClass('li-hover')},
		function(){$(this).removeClass('li-hover')}
	)
	
	$('.js-hover2').each(function(){
		$(this).hover(
			function(){$(this).addClass('list-hover')},
			function(){$(this).removeClass('list-hover')}
		)
	})
	//item5n
	$('.js-deco0').each(function(){
		var $this = $(this);
		$('li:nth-child(5n)',$this).addClass('item5n');
		$('li:nth-child(5n+1)',$this).addClass('item5n1');
		$('li:first',$this).removeClass('item5n').removeClass('item5n1').addClass('first');
		$('li:last',$this).removeClass('item5n').removeClass('item5n1');
	})
	//rank
	$('.js-rank0').each(function(){
		var $this = $(this);
		$('li',$this).each(function(i){
			$('.rank em',$(this)).text(i+1);
		}).bind('mouseenter',function(){
				$(this).addClass('on');
				$(this).siblings().removeClass('on')
			}
		)
		$('li:first',$this).triggerHandler('mouseenter');
		$('li:eq(0)',$this).addClass('no1')
		$('li:eq(1),li:eq(2)',$this).addClass('no23')
		$('li:odd',$this).addClass('odd')
	
	})
	//rank
	$('.js-rank1').each(function(){
		var $this = $(this);
		$('li',$this).each(function(i){
			$('em',$(this)).text(i+1);
		})
		$('li:eq(0)',$this).addClass('no1')
		$('li:eq(1),li:eq(2)',$this).addClass('no23')
	})
	
	//box0
	$('.box0').each(function(i){
		$(this).addClass('box0-z'+i);
	})
	
})