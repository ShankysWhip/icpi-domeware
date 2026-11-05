-- Orbit License Maker: asks who a license is for, then saves a signed license file on the Desktop and
-- puts the license code on the clipboard. It only works on the Mac that holds the signing key.
on run
	set tool to POSIX path of (path to resource "orbit-license")
	set keyFile to POSIX path of (path to application support from user domain) & "Orbit License Maker/private.key"
	try
		do shell script "test -f " & quoted form of keyFile
	on error
		display dialog "No signing key was found on this Mac:" & return & keyFile & return & return & "Orbit licenses can only be made on the Mac that holds the key." buttons {"OK"} default button 1 with icon stop with title "Orbit License Maker"
		return
	end try
	repeat
		set theName to my askFor("Name of the person who will use Orbit:", "")
		if theName is "" then return
		set theOrg to my askFor("Organisation (optional):", "")
		set theEmail to my askFor("Email (optional):", "")
		set theEnd to my askFor("Valid until (YYYY-MM-DD). Leave it empty for no end date:", (do shell script "date -v+1y +%Y-%m-%d"))
		set theMachine to my askFor("Computer ID (optional). Leave it empty so the license works on any computer, or paste the ID that Orbit shows on that person's computer:", "")
		set cmd to quoted form of tool & " make --name " & quoted form of theName & " --org " & quoted form of theOrg & " --email " & quoted form of theEmail & " --expires " & quoted form of theEnd & " --machine " & quoted form of theMachine
		try
			set out to do shell script cmd
		on error errMsg
			display dialog "The license was not made:" & return & return & errMsg buttons {"OK"} default button 1 with icon stop with title "Orbit License Maker"
			return
		end try
		set filePath to paragraph 1 of out
		set the clipboard to (paragraph 2 of out)
		set btn to button returned of (display dialog "License made for " & theName & "." & return & return & "Saved on the Desktop:" & return & filePath & return & return & "The license code is also on the clipboard, ready to paste into an email." buttons {"Make another", "Show the file", "Done"} default button "Done" with title "Orbit License Maker")
		if btn is "Show the file" then do shell script "open -R " & quoted form of filePath
		if btn is not "Make another" then exit repeat
	end repeat
end run

on askFor(prompt, def)
	return text returned of (display dialog prompt default answer def with title "Orbit License Maker")
end askFor
